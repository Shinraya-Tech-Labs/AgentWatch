import '@testing-library/jest-dom';

import { fireEvent, render, screen } from '@testing-library/react';
import React from 'react';

import {
  ContractAssessment,
  ProofAndGapsVisualizer,
  SensorCoverageInfo,
} from '../components/v3/ProofAndGapsVisualizer';
import type { CEvent } from '../lib/v3/types';

const mockEvents: CEvent[] = [
  {
    event_id: 'evt-001',
    interp_id: 'interp-1',
    normalizer: 'norm@1.0',
    derived_from: ['obs-auth-1'],
    kind: 'TOOL_INVOCATION',
    operation: 'auth.verify',
    status: 'OK',
    actor: 'agent:auth',
    object: 'tool:auth',
    facets: [],
    inputs: [],
    outputs: [],
    effects: [],
    time: {
      start: '2026-10-22T10:00:00.000Z',
      end: '2026-10-22T10:00:00.050Z',
      basis: 'sensor',
      duration_ms: 50,
      ordering_key: 'sensor_1:0001',
    },
    source_ids: [],
    parents: [{ relation: 'parent', key_space: 'span', value: 'span-root' }],
    run_key: null,
    run_id: 'run-100',
    run_basis: null,
    error: null,
    resources: {},
    attributes: { session_id: 'sess-99' },
    confidence: {
      observation: { value: 1, basis: 'declared', calibrated: false },
      attribution: { value: 1, basis: 'declared', calibrated: false },
    },
    missing: [],
  },
  {
    event_id: 'evt-002',
    interp_id: 'interp-1',
    normalizer: 'norm@1.0',
    derived_from: ['obs-pay-1'],
    kind: 'TOOL_INVOCATION',
    operation: 'payments.charge',
    status: 'OK',
    actor: 'agent:checkout',
    object: 'tool:pay',
    facets: [],
    inputs: [],
    outputs: [],
    effects: [],
    time: {
      start: '2026-10-22T10:00:01.000Z',
      end: '2026-10-22T10:00:01.100Z',
      basis: 'sensor',
      duration_ms: 100,
      ordering_key: 'sensor_1:0002',
    },
    source_ids: [],
    parents: [],
    run_key: null,
    run_id: 'run-100',
    run_basis: null,
    error: null,
    resources: {},
    attributes: { session_id: 'sess-99' },
    confidence: {
      observation: { value: 1, basis: 'declared', calibrated: false },
      attribution: { value: 1, basis: 'declared', calibrated: false },
    },
    missing: [],
  },
];

const supportedAssessment: ContractAssessment = {
  contract: 'auth-before-charge',
  verdict: 'supported',
  summary: 'Contract satisfied: charge had valid prior auth verification within 10s.',
  support_obs_ids: ['obs-auth-1'],
  refute_obs_ids: [],
  missing_facts: [],
  remedies: [],
};

const refutedAssessment: ContractAssessment = {
  contract: 'strict-audit-contract',
  verdict: 'refuted',
  summary: 'Refuted: transfer occurred without authorization. Audit sensor logged 0 drops.',
  support_obs_ids: [],
  refute_obs_ids: ['obs-pay-1'],
  missing_facts: [],
  remedies: [],
};

const inconclusiveAssessment: ContractAssessment = {
  contract: 'telemetry-completeness',
  verdict: 'inconclusive',
  summary: 'Inconclusive: required authorization log was missing due to sequence gap.',
  support_obs_ids: [],
  refute_obs_ids: [],
  missing_facts: [
    {
      field: 'auth_token',
      reason: 'SEQUENCE_GAP',
      required_for: 'auth.verify',
      candidate_sensors: ['auth_sensor_primary'],
      recoverability: 'INSTRUMENTABLE',
    },
  ],
  remedies: ['Enable high-durability ring buffer on auth_sensor_primary'],
};

const conflictingAssessment: ContractAssessment = {
  contract: 'clock-skew-invariance',
  verdict: 'conflicting_evidence',
  summary: 'Conflicting evidence: causal cycle or clock uncertainty detected between events.',
  support_obs_ids: [],
  refute_obs_ids: [],
  missing_facts: [],
  remedies: [],
};

const mockCoverage: SensorCoverageInfo[] = [
  {
    sensor_id: 'auth_sensor_primary',
    authoritative_for: ['auth.verify'],
    active: true,
    dropped_count: 0,
    clock_uncertainty_ms: 1.0,
    gaps: [],
  },
  {
    sensor_id: 'audit_edge_sensor',
    authoritative_for: ['payments.charge'],
    active: true,
    dropped_count: 14,
    clock_uncertainty_ms: 2.5,
    gaps: [
      {
        sensor_id: 'audit_edge_sensor',
        seq_lost_start: 100,
        seq_lost_end: 114,
        count: 14,
      },
    ],
  },
];

describe('ProofAndGapsVisualizer Component Tests', () => {
  it('renders all 4 epistemic verdict badges correctly (SUPPORTED, REFUTED, INCONCLUSIVE, CONFLICTING)', () => {
    const { rerender } = render(
      <ProofAndGapsVisualizer
        events={mockEvents}
        assessments={[supportedAssessment]}
        coverage={mockCoverage}
      />
    );
    expect(screen.getByTestId('assessment-verdict-badge')).toHaveTextContent('SUPPORTED');

    // Refuted state
    rerender(
      <ProofAndGapsVisualizer
        events={mockEvents}
        assessments={[refutedAssessment]}
        coverage={mockCoverage}
      />
    );
    expect(screen.getByTestId('assessment-verdict-badge')).toHaveTextContent('REFUTED');

    // Inconclusive state
    rerender(
      <ProofAndGapsVisualizer
        events={mockEvents}
        assessments={[inconclusiveAssessment]}
        coverage={mockCoverage}
      />
    );
    expect(screen.getByTestId('assessment-verdict-badge')).toHaveTextContent('INCONCLUSIVE');

    // Conflicting state
    rerender(
      <ProofAndGapsVisualizer
        events={mockEvents}
        assessments={[conflictingAssessment]}
        coverage={mockCoverage}
      />
    );
    expect(screen.getByTestId('assessment-verdict-badge')).toHaveTextContent('CONFLICTING');
  });

  it('highlights supporting and refuting witness nodes in the execution timeline', () => {
    render(
      <ProofAndGapsVisualizer
        events={mockEvents}
        assessments={[supportedAssessment, refutedAssessment]}
        coverage={mockCoverage}
      />
    );

    // evt-001 has obs-auth-1 which is in support_obs_ids -> SUPPORTING WITNESS badge
    expect(screen.getByText('SUPPORTING WITNESS')).toBeInTheDocument();

    // evt-002 has obs-pay-1 which is in refute_obs_ids -> REFUTING WITNESS badge
    expect(screen.getByText('REFUTING WITNESS')).toBeInTheDocument();
  });

  it('visually distinguishes direct declared links from inferred correlation links', () => {
    render(
      <ProofAndGapsVisualizer
        events={mockEvents}
        assessments={[supportedAssessment]}
        coverage={mockCoverage}
      />
    );

    // evt-001 has parents -> DECLARED
    expect(screen.getByText('DECLARED')).toBeInTheDocument();

    // evt-002 has no parents -> INFERRED
    expect(screen.getByText('INFERRED')).toBeInTheDocument();
  });

  it('renders interactive missing-fact callout box with copy remedy button for INCONCLUSIVE assessments', () => {
    render(
      <ProofAndGapsVisualizer
        events={mockEvents}
        assessments={[inconclusiveAssessment]}
        coverage={mockCoverage}
      />
    );

    expect(screen.getByTestId('missing-fact-callout')).toBeInTheDocument();
    expect(screen.getByText(/Reason: SEQUENCE_GAP/i)).toBeInTheDocument();
    expect(screen.getByText(/Enable high-durability ring buffer/i)).toBeInTheDocument();

    const copyBtn = screen.getByTestId('copy-remedy-button');
    expect(copyBtn).toHaveTextContent('Copy Remedy');
    fireEvent.click(copyBtn);
  });

  it('renders sensor completeness waterfall with active status and sequence gaps', () => {
    render(
      <ProofAndGapsVisualizer
        events={mockEvents}
        assessments={[supportedAssessment]}
        coverage={mockCoverage}
      />
    );

    expect(screen.getByTestId('sensor-row-auth_sensor_primary')).toBeInTheDocument();
    expect(screen.getByText('0 drops (clean)')).toBeInTheDocument();

    expect(screen.getByTestId('sensor-row-audit_edge_sensor')).toBeInTheDocument();
    expect(screen.getByText('14 dropped')).toBeInTheDocument();
    expect(screen.getByText(/Sequence Gap: \[100\.\.114\]/i)).toBeInTheDocument();
  });

  it('supports keyboard navigability for selecting timeline events', () => {
    const handleSelect = jest.fn();
    render(
      <ProofAndGapsVisualizer
        events={mockEvents}
        assessments={[supportedAssessment]}
        coverage={mockCoverage}
        onSelectEvent={handleSelect}
      />
    );

    const eventNode = screen.getByTestId('event-node-evt-001');
    fireEvent.keyDown(eventNode, { key: 'Enter', code: 'Enter' });
    expect(handleSelect).toHaveBeenCalledWith('evt-001');

    fireEvent.keyDown(eventNode, { key: ' ', code: 'Space' });
    expect(handleSelect).toHaveBeenCalledWith('evt-001');
  });
});
