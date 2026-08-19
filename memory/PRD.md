# Enterprise Frontend Renewal

## Original problem statement
i am connectig a repo i want to make its frontend like enterprize level frontend and dont touch the backend backend should remain the same make the buttons all the things in correct form

## Architecture decisions
- Kept the existing React/TypeScript route structure and API contracts unchanged.
- Applied the design system through the shared shell, primitives, and existing theme files rather than creating a parallel frontend.
- Used the already-installed Lucide icon system for navigation affordances and preserved the existing navy/light brand direction with stronger typography and hierarchy.

## Implemented
- Refined the persistent workspace shell with clearer navigation icons, active states, status context, wider content flow, responsive behavior, and stable test IDs.
- Improved shared page headers, cards, metrics, coverage, loading, error, empty, and password controls for accessibility and consistent interaction states.
- Added stable test IDs across landing, authentication, dashboard, admin, project, RFQ, and setup workflows.
- Updated enterprise typography and added a subtle canvas grid texture without changing backend behavior.
- Added the missing `@testing-library/dom` frontend test dependency and confirmed the production build passes.

## Prioritized backlog
- P0: Correct the managed frontend supervisor working directory from `/app/frontend` to the actual `/app/web` path so runtime smoke tests can execute.
- P0: Resolve the Node 20/webidl incompatibility affecting jsdom/Vitest workers.
- P1: Move demo credential configuration out of the frontend source and confirm the intended demo-account contract.
- P2: Add a compact mobile drawer/bottom navigation for very narrow screens.

## Next tasks
- Restore the managed frontend process, then validate all route flows in a browser.
- Run backend route regression checks once the service working-directory configuration is corrected.