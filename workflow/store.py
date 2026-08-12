from datetime import date

from workflow.models.project import Project


class WorkflowStore:
    """In-memory store for workflow entities. Phase 1 carries no database —
    persistence arrives in Phase 2, and every method here is written so that
    swapping the dicts for a repository does not change a caller."""

    def __init__(self) -> None:
        self._projects: dict[str, Project] = {}

    # -- projects ---------------------------------------------------------

    def create_project(
        self,
        name: str,
        code: str,
        client: str,
        location: str,
        live_period_start: date,
        live_period_end: date,
        currency: str = "AED",
    ) -> Project:
        project = Project(
            name=name,
            code=code,
            client=client,
            location=location,
            live_period_start=live_period_start,
            live_period_end=live_period_end,
            currency=currency,
        )
        self._projects[project.id] = project
        return project

    def get_project(self, project_id: str) -> Project | None:
        return self._projects.get(project_id)

    def rename_project(self, project_id: str, new_name: str) -> Project:
        project = self._projects.get(project_id)
        if project is None:
            raise KeyError(f"Unknown project: {project_id}")
        updated = project.model_copy(update={"name": new_name})
        self._projects[project_id] = updated
        return updated
