"""The RFQ workflow: projects, items, the eight-stage state machine and its gates.

Deliberately import-free at package level. `workflow.models.rfq` imports
`workflow.stages`, and `workflow.store` imports both, so anything re-exported
here would close that into a cycle.
"""
