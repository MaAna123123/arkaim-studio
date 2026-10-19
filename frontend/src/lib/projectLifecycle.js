function isAbortError(error) {
  return error?.name === 'AbortError';
}

function projectIdentityMatches(project, projectId) {
  if (!project || typeof project !== 'object') return false;
  return project.project_id === projectId && project.video_id === projectId;
}

function restartIdentityMatches(snapshot, projectId) {
  return Boolean(
    snapshot
    && typeof snapshot.operation_id === 'string'
    && snapshot.operation_id
    && snapshot.video_id === projectId
    && (!snapshot.operation_type || snapshot.operation_type === 'transcription'),
  );
}

export function canUseRecentProjects({ serverOnline, hasVideo }) {
  return Boolean(serverOnline) && !hasVideo;
}

export function createProjectLifecycleController({
  apiBase,
  fetchImpl = (...args) => fetch(...args),
  readResponse,
  autosave,
  cancelAutosaveRequest = () => {},
  hydrateProject = () => {},
  clearMissingProject = () => {},
  receiveRecentProjects = () => {},
  receiveRestart = () => {},
  receiveDelete = () => {},
  setPendingAction = () => {},
  setError = () => {},
}) {
  let hydratedProjectId = null;
  let detailController = null;
  let detailGeneration = 0;
  let listController = null;
  let listGeneration = 0;
  let restartController = null;
  let restartGeneration = 0;
  let deleteController = null;
  let deleteGeneration = 0;
  let pendingToken = null;
  let nextPendingId = 1;

  const clearPending = (token = pendingToken) => {
    if (!token || token !== pendingToken) return;
    pendingToken = null;
    setPendingAction(null);
  };

  const beginPending = (projectId, action) => {
    const token = { id: nextPendingId++, projectId, action };
    pendingToken = token;
    setPendingAction({ projectId, action });
    return token;
  };

  const cancelAutosave = () => {
    autosave?.cancel?.();
    cancelAutosaveRequest();
  };

  const cancelRecent = () => {
    listGeneration += 1;
    listController?.abort();
    listController = null;
  };

  const cancelDetail = () => {
    detailGeneration += 1;
    detailController?.abort();
    detailController = null;
  };

  const cancelRestart = () => {
    restartGeneration += 1;
    restartController?.abort();
    restartController = null;
    if (pendingToken?.action === 'restart') clearPending(pendingToken);
  };

  const cancelDelete = () => {
    deleteGeneration += 1;
    deleteController?.abort();
    deleteController = null;
    if (pendingToken?.action === 'delete') clearPending(pendingToken);
  };

  const prepareProjectTransition = () => {
    cancelDetail();
    cancelRestart();
    cancelRecent();
    cancelAutosave();
    hydratedProjectId = null;
  };

  const loadProject = (projectId, { origin = 'recent', resume = false } = {}) => {
    if (!projectId) return Promise.resolve(null);
    prepareProjectTransition();
    const token = origin === 'restore'
      ? null
      : beginPending(projectId, resume ? 'resume' : 'open');
    const generation = detailGeneration;
    const controller = new AbortController();
    detailController = controller;

    return (async () => {
      let keepPending = false;
      try {
        const response = await fetchImpl(`${apiBase}/api/projects/${projectId}`, {
          signal: controller.signal,
        });
        const project = await readResponse(response);
        if (
          generation !== detailGeneration
          || detailController !== controller
        ) return null;
        if (!projectIdentityMatches(project, projectId)) {
          throw new Error('Ответ проекта не соответствует запрошенному проекту');
        }
        if (project.source_exists === false) {
          clearMissingProject(project);
          return project;
        }

        let resumeOperationId = null;
        if (resume) {
          resumeOperationId = project.transcription?.operation_id;
          if (!resumeOperationId || project.transcription?.resume_available === false) {
            const message = project.transcription?.diagnostic?.message
              || 'Сохранённый прогресс нельзя продолжить';
            setError(message);
            return null;
          }
        }

        hydrateProject(project, { resumeOperationId });
        hydratedProjectId = projectId;
        keepPending = resume;
        return project;
      } catch (error) {
        if (
          !isAbortError(error)
          && generation === detailGeneration
          && detailController === controller
        ) {
          const prefix = origin === 'restore' ? 'Не удалось восстановить проект: ' : '';
          setError(`${prefix}${error.message}`);
        }
        return null;
      } finally {
        if (detailController === controller) detailController = null;
        if (!keepPending) clearPending(token);
      }
    })();
  };

  const syncRecentProjects = ({ serverOnline, hasVideo }) => {
    cancelRecent();
    if (!canUseRecentProjects({ serverOnline, hasVideo })) return null;
    const generation = listGeneration;
    const controller = new AbortController();
    listController = controller;
    return (async () => {
      try {
        const response = await fetchImpl(`${apiBase}/api/projects?limit=20`, {
          signal: controller.signal,
        });
        const data = await readResponse(response);
        if (
          generation !== listGeneration
          || listController !== controller
        ) return null;
        receiveRecentProjects(data);
        return data;
      } catch (error) {
        if (
          !isAbortError(error)
          && generation === listGeneration
          && listController === controller
        ) setError(`Не удалось загрузить проекты: ${error.message}`);
        return null;
      } finally {
        if (listController === controller) listController = null;
      }
    })();
  };

  const restartProject = (projectId) => {
    if (!projectId) return Promise.resolve(null);
    cancelDetail();
    cancelRestart();
    cancelRecent();
    cancelAutosave();
    hydratedProjectId = null;
    const token = beginPending(projectId, 'restart');
    const generation = restartGeneration;
    const controller = new AbortController();
    restartController = controller;
    return (async () => {
      try {
        const response = await fetchImpl(
          `${apiBase}/api/projects/${projectId}/transcription/restart`,
          { method: 'POST', signal: controller.signal },
        );
        const snapshot = await readResponse(response);
        if (
          generation !== restartGeneration
          || restartController !== controller
        ) return null;
        if (!restartIdentityMatches(snapshot, projectId)) {
          throw new Error('Ответ операции не соответствует выбранному проекту');
        }
        receiveRestart(projectId, snapshot);
        return snapshot;
      } catch (error) {
        if (
          !isAbortError(error)
          && generation === restartGeneration
          && restartController === controller
        ) setError(error.message);
        return null;
      } finally {
        if (restartController === controller) restartController = null;
        clearPending(token);
      }
    })();
  };

  const deleteProject = (projectId) => {
    if (!projectId) return Promise.resolve(null);
    if (deleteController) return Promise.resolve(null);
    cancelDetail();
    cancelRestart();
    cancelRecent();
    cancelAutosave();
    const token = beginPending(projectId, 'delete');
    const generation = deleteGeneration;
    const controller = new AbortController();
    deleteController = controller;
    return (async () => {
      try {
        const response = await fetchImpl(`${apiBase}/api/projects/${projectId}`, {
          method: 'DELETE',
          signal: controller.signal,
        });
        const result = await readResponse(response);
        if (generation !== deleteGeneration || deleteController !== controller) return null;
        if (result?.deleted !== true || result?.project_id !== projectId) {
          throw new Error('Ответ удаления не соответствует выбранному проекту');
        }
        if (hydratedProjectId === projectId) hydratedProjectId = null;
        receiveDelete(projectId);
        return result;
      } catch (error) {
        if (
          !isAbortError(error)
          && generation === deleteGeneration
          && deleteController === controller
        ) setError(error.message);
        return null;
      } finally {
        if (deleteController === controller) deleteController = null;
        clearPending(token);
      }
    })();
  };

  const cancelAll = () => {
    cancelDetail();
    cancelRestart();
    cancelRecent();
    cancelAutosave();
    hydratedProjectId = null;
    clearPending(pendingToken);
  };

  return {
    loadProject,
    restartProject,
    deleteProject,
    syncRecentProjects,
    cancelRecent,
    cancelDetail,
    completePending(projectId, action) {
      if (pendingToken?.projectId === projectId && pendingToken?.action === action) {
        clearPending(pendingToken);
      }
    },
    adoptHydratedProject(projectId) {
      hydratedProjectId = projectId || null;
    },
    scheduleAutosave({ projectId, serverOnline, editor }) {
      if (!serverOnline || !projectId || projectId !== hydratedProjectId) {
        autosave?.cancel?.();
        return false;
      }
      return autosave?.schedule?.({ projectId, hydratedProjectId, editor }) ?? false;
    },
    cancelAll,
    dispose() {
      cancelAll();
      cancelDelete();
    },
  };
}
