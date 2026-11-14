import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory


class CadPreviewService:
    """Triangula STEP para el visor web sin convertir STL en entrada de usuario."""

    def __init__(self, cache_root: Path):
        self.cache_root = cache_root

    @staticmethod
    def _component_manifest(root, digest):
        try:
            manifest = json.loads((root / 'manifest.json').read_text())
            if (not isinstance(manifest, dict) or manifest.get('version') != 3 or manifest.get('digest') != digest
                    or not isinstance(manifest.get('parts'), list) or not manifest['parts']):
                return None
            for part in manifest['parts']:
                for key in ('file', 'cad_file'):
                    name = part[key]
                    if not isinstance(name, str) or Path(name).name != name:
                        return None
                    path = root / name
                    if path.is_symlink() or not path.is_file():
                        return None
            return manifest
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _reuse_component_cache(self, digest):
        """Reuse immutable, atomically published assets within this category."""
        namespaces = {('Temporales', 'step_previews'), ('cache', 'monte-carlo-step-components'),
                      ('cache', 'phits-step-components')}
        if (self.cache_root.parent.name, self.cache_root.name) not in namespaces:
            return None
        category = self.cache_root.parent.parent
        target = self.cache_root / (digest + '-components')
        for parent, name in sorted(namespaces):
            peer = category / parent / name / target.name
            if peer == target or not peer.resolve().is_relative_to(category.resolve()):
                continue
            manifest = self._component_manifest(peer, digest)
            if manifest is None:
                continue
            self.cache_root.mkdir(parents=True, exist_ok=True)
            target.mkdir(exist_ok=True)
            with TemporaryDirectory(dir=self.cache_root) as temporary:
                staging = Path(temporary)
                for part in manifest['parts']:
                    for key in ('file', 'cad_file'):
                        filename = part[key]
                        if (staging / filename).exists():
                            continue
                        try:
                            os.link(peer / filename, staging / filename)
                        except OSError:
                            shutil.copy2(peer / filename, staging / filename)
                for path in staging.iterdir():
                    path.replace(target / path.name)
                staged = staging / 'manifest.json'
                staged.write_text(json.dumps(manifest, indent=2))
                staged.replace(target / staged.name)
            return manifest
        return None

    @staticmethod
    def material_tag(component_name: str) -> str:
        """Obtiene el tag DAGMC que antecede al primer guion bajo del componente."""
        tag = component_name.strip().split("_", 1)[0].strip().lower()
        if not tag or not re.fullmatch(r"[a-z0-9.-]+", tag):
            raise ValueError(
                "El componente STEP '%s' no contiene un nombre de material válido"
                % component_name
            )
        return tag

    def derive_mesh(self, step_path: Path) -> Path:
        digest = hashlib.sha256(step_path.read_bytes()).hexdigest()[:20]
        self.cache_root.mkdir(parents=True, exist_ok=True)
        mesh_path = self.cache_root / (digest + ".stl")
        if mesh_path.is_file():
            return mesh_path

        from cadquery import exporters, importers

        model = importers.importStep(str(step_path))
        # CadQuery/OCP reconoce el formato también por la extensión. El archivo
        # temporal conserva .stl y solo se publica mediante un reemplazo atómico.
        temporary = mesh_path.with_name(mesh_path.stem + ".tmp.stl")
        exporters.export(
            model,
            str(temporary),
            exportType="STL",
            tolerance=0.08,
            angularTolerance=0.08,
        )
        temporary.replace(mesh_path)
        return mesh_path

    def derive_component_meshes(self, step_path: Path):
        """Triangula cada componente STEP conservando su nombre para el visor."""
        digest = hashlib.sha256(step_path.read_bytes()).hexdigest()[:20]
        component_root = self.cache_root / (digest + "-components")
        manifest_path = component_root / "manifest.json"
        manifest = self._component_manifest(component_root, digest)
        if manifest is not None:
            return manifest
        manifest = self._reuse_component_cache(digest)
        if manifest is not None:
            return manifest

        from cadquery import Shape, exporters
        from OCP.IFSelect import IFSelect_RetDone
        from OCP.STEPCAFControl import STEPCAFControl_Reader
        from OCP.TCollection import TCollection_ExtendedString
        from OCP.TDataStd import TDataStd_Name
        from OCP.TDF import TDF_Label, TDF_LabelSequence
        from OCP.TDocStd import TDocStd_Document
        from OCP.TopLoc import TopLoc_Location
        from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ShapeTool

        document = TDocStd_Document(TCollection_ExtendedString("IRRADIUM STEP"))
        reader = STEPCAFControl_Reader()
        reader.SetNameMode(True)
        if reader.ReadFile(str(step_path)) != IFSelect_RetDone or not reader.Transfer(document):
            raise ValueError("STEP File could not be loaded with component metadata")

        shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
        free_shapes = TDF_LabelSequence()
        shape_tool.GetFreeShapes(free_shapes)

        def label_name(label):
            attribute = TDataStd_Name()
            if label.FindAttribute(TDataStd_Name.GetID_s(), attribute):
                return attribute.Get().ToExtString()
            referred = TDF_Label()
            if XCAFDoc_ShapeTool.GetReferredShape_s(label, referred):
                attribute = TDataStd_Name()
                if referred.FindAttribute(TDataStd_Name.GetID_s(), attribute):
                    return attribute.Get().ToExtString()
            return "component"

        labels = []

        def collect(label, parent_location):
            referred = TDF_Label()
            definition = referred if XCAFDoc_ShapeTool.GetReferredShape_s(label, referred) else label
            components = TDF_LabelSequence()
            if XCAFDoc_ShapeTool.GetComponents_s(definition, components, False) and components.Length():
                location = parent_location.Multiplied(XCAFDoc_ShapeTool.GetLocation_s(label))
                for index in range(1, components.Length() + 1):
                    collect(components.Value(index), location)
            else:
                labels.append((label, definition, parent_location))

        for root_index in range(1, free_shapes.Length() + 1):
            collect(free_shapes.Value(root_index), TopLoc_Location())

        component_root.mkdir(parents=True, exist_ok=True)
        parts = []
        used_names = set()
        with TemporaryDirectory(dir=component_root) as temporary:
            staging = Path(temporary)
            for index, (label, definition, parent_location) in enumerate(labels, start=1):
                name = label_name(label).strip()
                if name.isdecimal() or not name:
                    name = label_name(definition).strip()
                original_name = name or ("component_%03d" % index)
                safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", original_name).strip("._")
                safe_name = safe_name or ("component_%03d" % index)
                candidate = safe_name
                suffix = 2
                while candidate.lower() in used_names:
                    candidate = "%s_%d" % (safe_name, suffix)
                    suffix += 1
                used_names.add(candidate.lower())
                filename = candidate + ".stl"
                shape = XCAFDoc_ShapeTool.GetShape_s(label).Moved(parent_location)
                native_shape = Shape.cast(shape)
                exporters.export(native_shape, str(staging / filename), exportType="STL",
                                 tolerance=0.08, angularTolerance=0.08)
                cad_filename = candidate + ".step"
                exporters.export(native_shape, str(staging / cad_filename), exportType="STEP")
                parts.append({
                    "name": original_name, "material": self.material_tag(original_name), "file": filename,
                    "cad_file": cad_filename,
                    "transparent": original_name.lower() in {"air_001", "air_001_part"},
                })
            manifest = {"version": 3, "digest": digest, "parts": parts}
            for part in parts:
                (staging / part["file"]).replace(component_root / part["file"])
                (staging / part["cad_file"]).replace(component_root / part["cad_file"])
            staged_manifest = staging / manifest_path.name
            staged_manifest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            staged_manifest.replace(manifest_path)
        return manifest

    def component_mesh_path(self, digest: str, filename: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{20}", digest):
            raise ValueError("Invalid preview digest")
        if Path(filename).name != filename or not filename.lower().endswith(".stl"):
            raise ValueError("Invalid component filename")
        path = self.cache_root / (digest + "-components") / filename
        if not path.is_file():
            raise FileNotFoundError(filename)
        return path

    def component_cad_path(self, digest: str, filename: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{20}", digest):
            raise ValueError("Invalid preview digest")
        if Path(filename).name != filename or not filename.lower().endswith(".step"):
            raise ValueError("Invalid CAD filename")
        path = self.cache_root / (digest + "-components") / filename
        if not path.is_file():
            raise FileNotFoundError(filename)
        return path
