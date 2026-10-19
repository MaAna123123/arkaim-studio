import { getProjectCardState } from '../lib/projectState';


function pendingForProject(pendingAction, projectId) {
  if (!pendingAction || typeof pendingAction !== 'object' || Array.isArray(pendingAction)) return null;
  const pendingProjectId = pendingAction.projectId ?? pendingAction.project_id;
  if (pendingProjectId !== projectId) return null;
  return pendingAction.action ?? pendingAction.pendingAction ?? true;
}

function run(handler, project) {
  Promise.resolve(handler?.(project)).catch(() => {});
}

export default function RecentProjects({
  projects = [],
  pendingAction,
  onOpen,
  onResume,
  onRestart,
  onDelete,
}) {
  if (!Array.isArray(projects) || projects.length === 0) return null;

  const confirmRestart = (project) => {
    if (typeof window === 'undefined') return;
    if (!window.confirm('Начать транскрипцию заново? Сохранённый прогресс будет заменён.')) return;
    run(onRestart, project);
  };

  const confirmDelete = (project) => {
    if (typeof window === 'undefined') return;
    const title = project.title || project.filename || 'Без названия';
    if (!window.confirm(`Удалить проект «${title}» полностью?\n\nИсходное видео, расшифровка, прогресс и все клипы будут удалены без возможности восстановления.`)) return;
    run(onDelete, project);
  };

  return (
    <section className="recent-projects" aria-labelledby="recent-projects-title">
      <div className="recent-projects-heading">
        <div>
          <p className="arkaim-eyebrow">рабочее пространство</p>
          <h2 id="recent-projects-title">Недавние проекты</h2>
        </div>
        <span>{projects.length}</span>
      </div>

      <div className="recent-projects-grid">
        {projects.map((project) => {
          const state = getProjectCardState(project, {
            pendingAction: pendingForProject(pendingAction, project.project_id),
          });
          const title = project.title || project.filename || 'Без названия';
          const detailsOnly = !state.actions.open && state.actions.details;
          const deleting = pendingForProject(pendingAction, project.project_id) === 'delete';

          return (
            <article className={`recent-project-card recent-project-card-${state.kind}`} key={project.project_id || title}>
              <div className="recent-project-card-topline">
                <span className={`recent-project-status recent-project-status-${state.kind}`}>{state.label}</span>
                {project.filename && <span className="recent-project-filename">{project.filename}</span>}
              </div>
              <h3 title={title}>{title}</h3>

              {state.progress !== null && (
                <div className="recent-project-progress" aria-label={`Прогресс: ${state.progress}%`}>
                  <div className="recent-project-progress-copy"><span>Прогресс</span><strong>{state.progress}%</strong></div>
                  <div className="progress-bar-track" aria-hidden="true">
                    <div className="progress-bar-fill" style={{ width: `${state.progress}%` }} />
                  </div>
                </div>
              )}

              <div className="recent-project-actions">
                <button type="button" className="btn btn-secondary btn-sm" disabled={deleting} onClick={() => run(onOpen, project)}>
                  {detailsOnly ? 'Детали проекта' : 'Открыть'}
                </button>
                {state.actions.resume && (
                  <button
                    type="button"
                    className="btn btn-success btn-sm"
                    disabled={deleting || state.actionDisabled.resume}
                    aria-label={state.resumeLabel}
                    onClick={() => run(onResume, project)}
                  >
                    {state.resumeLabel}
                  </button>
                )}
                {state.actions.restart && (
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    disabled={deleting || state.actionDisabled.restart}
                    onClick={() => confirmRestart(project)}
                  >
                    Начать заново
                  </button>
                )}
                <button
                  type="button"
                  className="btn btn-danger btn-sm"
                  disabled={deleting}
                  aria-label={`Удалить проект «${title}»`}
                  onClick={() => confirmDelete(project)}
                >
                  {deleting ? 'Удаляю...' : 'Удалить'}
                </button>
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}
