export interface ComplianceMatrixProps {
  slug: string
  projectName: string
}

// Implemented by the Compliance Matrix page subagent.
export function ComplianceMatrix(_props: ComplianceMatrixProps) {
  return <div className="state">Compliance matrix placeholder</div>
}
