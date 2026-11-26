/**
 * The recorded agent pipeline, in order.
 *
 * This is an observability view: each row is a step the orchestrator actually
 * executed for this complaint, with its status, model and timing. It carries no
 * decision - a `succeeded` agent step is not an approved outcome.
 */

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeaderCell,
  TableRow,
  TableWrapper,
} from '@/components/ui/table'
import { EmptyState } from '@/components/common/states'
import { formatDateTime, formatPercent } from '@/lib/format'
import { AGENT_LABELS, humanize, type Tone } from '@/lib/labels'
import type { AgentExecutionStatus, OfficerAgentStep } from '@/types'

const STEP_TONES: Record<AgentExecutionStatus, Tone> = {
  succeeded: 'success',
  failed: 'danger',
  awaiting_human: 'warning',
  skipped: 'warning',
  started: 'info',
}

export function AgentStepsPanel({ steps }: { steps: OfficerAgentStep[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Agent pipeline trace</CardTitle>
        <CardDescription>
          The recorded agent steps, in order, with their status and timing. This is an
          observability record, not a decision.
        </CardDescription>
      </CardHeader>

      <CardContent>
        {steps.length === 0 ? (
          <EmptyState
            title="No agent steps recorded"
            description="No agent activity has been recorded for this complaint."
          />
        ) : (
          <TableWrapper>
            <Table>
              <TableHead>
                <TableRow>
                  <TableHeaderCell>Step</TableHeaderCell>
                  <TableHeaderCell>Agent</TableHeaderCell>
                  <TableHeaderCell>Status</TableHeaderCell>
                  <TableHeaderCell>Confidence</TableHeaderCell>
                  <TableHeaderCell>Model</TableHeaderCell>
                  <TableHeaderCell>Duration</TableHeaderCell>
                  <TableHeaderCell>Started</TableHeaderCell>
                  <TableHeaderCell>Completed</TableHeaderCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {steps.map((step, index) => (
                  <TableRow key={`${step.agent_name}-${step.step_index ?? index}`}>
                    <TableCell className="tabular-nums text-[var(--muted-foreground)]">
                      {step.step_index ?? '-'}
                    </TableCell>
                    <TableCell className="font-medium">{AGENT_LABELS[step.agent_name]}</TableCell>
                    <TableCell>
                      <Badge tone={STEP_TONES[step.status]}>{humanize(step.status)}</Badge>
                    </TableCell>
                    <TableCell className="tabular-nums">{formatPercent(step.confidence)}</TableCell>
                    <TableCell className="text-[var(--muted-foreground)]">
                      {step.model_name ?? '-'}
                    </TableCell>
                    <TableCell className="tabular-nums">
                      {step.duration_ms === null ? '-' : `${step.duration_ms} ms`}
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-[var(--muted-foreground)]">
                      {formatDateTime(step.started_at)}
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-[var(--muted-foreground)]">
                      {formatDateTime(step.completed_at)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableWrapper>
        )}
      </CardContent>
    </Card>
  )
}
