/**
 * vtk.js locates the end of raw appended data with /\n\s*<\/AppendedData>/.
 * That also consumes a valid zlib checksum ending in newline/whitespace.
 * Use the declared block lengths to expose identical compressed bytes as
 * inline binary arrays. The source file and numerical values stay untouched.
 */
export function normalizeRawVti(buffer: ArrayBuffer): ArrayBuffer {
  const bytes = new Uint8Array(buffer);
  const text = new TextDecoder('latin1').decode(bytes);
  const appended = /<AppendedData\b[^>]*encoding=["']raw["'][^>]*>\s*_/.exec(text);
  if (!appended) return buffer;
  const closing = text.lastIndexOf('</AppendedData>');
  if (closing < appended.index) throw new Error('El VTI no cierra sus datos binarios.');
  const start = appended.index + appended[0].length;
  const header = new TextDecoder().decode(bytes.subarray(0, appended.index));
  const width = /header_type=["']UInt64["']/.test(header) ? 8 : 4;
  const littleEndian = !/byte_order=["']BigEndian["']/.test(header);
  const compressor = /compressor=["']([^"']+)["']/.exec(header)?.[1];
  if (compressor && compressor !== 'vtkZLibDataCompressor') return buffer;
  const view = new DataView(buffer);
  const integer = (offset: number) => {
    if (offset < start || offset + width > closing) throw new Error('Cabecera binaria VTI incompleta.');
    const value = width === 8 ? Number(view.getBigUint64(offset, littleEndian)) : view.getUint32(offset, littleEndian);
    if (!Number.isSafeInteger(value)) throw new Error('El bloque VTI supera el tamaño admitido.');
    return value;
  };
  const normalized = header.replace(/<DataArray\b[^>]*\/>/g, (tag) => {
    if (!/format=["']appended["']/.test(tag)) return tag;
    const offset = Number(/\boffset=["'](\d+)["']/.exec(tag)?.[1]);
    if (!Number.isSafeInteger(offset) || offset < 0) throw new Error('Offset binario VTI no válido.');
    const first = start + offset;
    const blocks = integer(first);
    let length: number;
    if (compressor) {
      if (blocks > Math.floor((closing - first) / width) - 3) throw new Error('Tabla de bloques VTI incompleta.');
      length = (3 + blocks) * width;
      for (let index = 0; index < blocks; index++) length += integer(first + (3 + index) * width);
    } else length = width + blocks;
    if (!Number.isSafeInteger(length) || first + length > closing) throw new Error('Datos binarios VTI incompletos.');
    let binary = '';
    for (let index = first; index < first + length; index += 32768) {
      binary += String.fromCharCode(...bytes.subarray(index, Math.min(first + length, index + 32768)));
    }
    return tag.replace(/format=["']appended["']/, 'format="binary"')
      .replace(/\s+offset=["']\d+["']/, '').slice(0, -2) + `>${btoa(binary)}</DataArray>`;
  });
  const tail = new TextDecoder().decode(bytes.subarray(closing + '</AppendedData>'.length));
  return new TextEncoder().encode(normalized + tail).buffer;
}
