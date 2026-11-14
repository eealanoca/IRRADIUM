'use client';

import { useEffect, useState } from 'react';
import { Activity, ArrowUpRight } from 'lucide-react';
import { API_URL, apiGet } from '../../lib/api';
import type { IrradiationCase } from '../../lib/types';
import StudyProgressBar from '../../features/route/StudyProgressBar';
import { preferredActiveJobIndex, requestedPositionCount } from '../../lib/studyProgress';

const activeStates = new Set(['queued', 'running', 'cancelling', 'pausing']);
type Job = { state: string; run_id?: string; stage?: string; position_name?: string; position_count?: number; route_study?: Parameters<typeof requestedPositionCount>[0] };
type Study = { run_id: string; count: number; requested_position_count?: number; accumulated_error_target?: number;
  interval_precision?: { relative_error_maximum?: number | null } };
type Snapshot = { caseId: string; calibrationCaseId?: string; name: string; job: Job; study?: Study };

export default function StudyActivityRibbon({ hidden, onOpen }: { hidden: boolean; onOpen: (caseId: string, calibrationCaseId?: string) => void }) {
  const [snapshot, setSnapshot] = useState<Snapshot>();
  const categoryApi = API_URL;
  useEffect(() => {
    if (hidden) return;
    let current = true;
    let timer: ReturnType<typeof setTimeout>;
    let chosen: IrradiationCase | undefined;
    let study: Study | undefined;
    let lastArchive = 0;
    async function poll() {
      try {
        let job: Job | undefined;
        if (chosen) job = await apiGet<Job>(`/api/cases/${chosen.case_id}/monte-carlo`);
        if (!job || !activeStates.has(job.state) || job.state === 'queued') {
          chosen = undefined;
          study = undefined;
          const { cases } = await apiGet<{ cases: IrradiationCase[] }>('/api/cases');
          const candidates = cases.filter((item) => item.workflow === 'suspended_litters');
          const jobs = await Promise.allSettled(candidates.map((item) => apiGet<Job>(`/api/cases/${item.case_id}/monte-carlo`)));
          const index = preferredActiveJobIndex(jobs.map((result) => result.status === 'fulfilled' ? result.value.state : ''));
          if (index >= 0) {
            chosen = candidates[index];
            job = (jobs[index] as PromiseFulfilledResult<Job>).value;
            lastArchive = 0;
          }
        }
        if (!current) return;
        if (chosen && job && activeStates.has(job.state)) {
          if (Date.now() - lastArchive > 5000 || study?.run_id !== job.run_id) {
            const archive = await apiGet<{ studies: Study[] }>(`/api/route/cases/${chosen.case_id}/studies`);
            study = archive.studies.find((item) => item.run_id === job!.run_id);
            lastArchive = Date.now();
          }
          if (current) setSnapshot({ caseId: chosen.case_id, calibrationCaseId: chosen.calibration_case_id, name: chosen.name, job, study });
        } else setSnapshot(undefined);
      } catch { /* The existing connection indicator reports API availability. */ }
      finally { if (current) timer = setTimeout(() => void poll(), chosen ? 2500 : 10000); }
    }
    void poll();
    return () => { current = false; clearTimeout(timer); };
  }, [hidden, categoryApi]);
  if (hidden || !snapshot) return null;
  const { job, study, name } = snapshot;
  return <aside className="global-study-ribbon" aria-label="Simulación activa">
    <Activity size={16}/><div className="global-study-name"><strong>{name}</strong><small>{job.state === 'pausing' ? 'Pausa solicitada · Terminando posición' : job.position_name ? `Calculando ${job.position_name}` : 'Preparando el estudio'}</small></div>
    <StudyProgressBar compact completed={study?.count ?? 0} total={study?.requested_position_count ?? requestedPositionCount(job.route_study) ?? job.position_count ?? 0}
      target={study?.accumulated_error_target} maximum={study?.interval_precision?.relative_error_maximum} stage={job.stage} state={job.state}/>
    <button type="button" className="button ghost" onClick={() => onOpen(snapshot.caseId, snapshot.calibrationCaseId)}>Ver resultados<ArrowUpRight size={14}/></button>
  </aside>;
}
