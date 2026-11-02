import { useEffect, useMemo, useRef, useState } from 'react';

import { edgeStyle, edgesOf, layeredLayout } from '../../lib/v3/graph';
import type { InfoEvidenceAttrs, NodeDesc, Relation } from '../../lib/v3/types';

interface Props {
  nodes: NodeDesc[];
  relations: Relation[];
  selected?: string | null;
  highlight?: Set<string> | null;
  onSelect?: (node: string) => void;
  height?: number;
}

function edgeTitle(rel: Relation): string {
  const a = rel.attributes as InfoEvidenceAttrs;
  const base = `${rel.view} ${rel.type} · ${rel.basis}${rel.evidence_class ? ` · ${rel.evidence_class}` : ''}`;
  if (!a?.evidence_type) return `${base} · confidence ${rel.confidence}`;
  const amb =
    a.resolution === 'AMBIGUOUS'
      ? ' · AMBIGUOUS candidate (not an information flow)'
      : a.strength === 'NONE'
        ? ' · similarity only (not an information flow)'
        : '';
  return `${base} · ${a.evidence_type} · ${a.strength} · ${a.mode ?? ''}${amb}`;
}

const STATUS_STROKE: Record<string, string> = {
  OK: '#10b981',
  ERROR: '#f43f5e',
  TIMEOUT: '#f43f5e',
  UNKNOWN: '#71717a',
};

export function GraphView({
  nodes,
  relations,
  selected,
  highlight,
  onSelect,
  height = 620,
}: Props) {
  const edges = useMemo(() => edgesOf(relations), [relations]);
  const layout = useMemo(
    () =>
      layeredLayout(
        nodes.map((n) => n.node),
        edges,
      ),
    [nodes, edges],
  );
  const byId = useMemo(() => new Map(nodes.map((n) => [n.node, n])), [nodes]);
  // view: (x, y) is the graph coordinate shown at the left edge / vertical centre; k = zoom
  const [view, setView] = useState({ x: -40, y: 0, k: 1 });
  const box = useRef<HTMLDivElement | null>(null);
  const drag = useRef<{ x: number; y: number } | null>(null);

  const bounds = useMemo(() => {
    let maxX = 0;
    let maxY = 0;
    for (const p of layout.values()) {
      maxX = Math.max(maxX, p.x);
      maxY = Math.max(maxY, Math.abs(p.y));
    }
    return { maxX, maxY };
  }, [layout]);

  const fit = () => {
    const width = box.current?.clientWidth ?? 1000;
    const k = Math.max(
      0.1,
      Math.min(1.2, width / (bounds.maxX + 240), height / (2 * bounds.maxY + 80)),
    );
    setView({ x: -40 / k, y: 0, k });
  };
  useEffect(fit, [bounds.maxX, bounds.maxY, height]);

  const dim = (id: string) => (highlight && !highlight.has(id) ? 0.12 : 1);

  return (
    <div
      ref={box}
      className="relative select-none overflow-hidden rounded border border-zinc-800 bg-black/40"
      style={{ height }}
    >
      <div className="absolute right-2 top-2 z-10 flex gap-1 font-mono text-[10px]">
        <button
          className="rounded border border-zinc-700 px-1.5"
          onClick={() => setView((v) => ({ ...v, k: v.k * 1.25 }))}
        >
          +
        </button>
        <button
          className="rounded border border-zinc-700 px-1.5"
          onClick={() => setView((v) => ({ ...v, k: v.k / 1.25 }))}
        >
          −
        </button>
        <button className="rounded border border-zinc-700 px-1.5" onClick={() => fit()}>
          fit
        </button>
      </div>
      <svg
        role="img"
        aria-label="graph"
        width="100%"
        height="100%"
        onWheel={(e) =>
          setView((v) => ({
            ...v,
            k: Math.max(0.1, Math.min(4, v.k * (e.deltaY < 0 ? 1.1 : 0.9))),
          }))
        }
        onMouseDown={(e) => (drag.current = { x: e.clientX, y: e.clientY })}
        onMouseUp={() => (drag.current = null)}
        onMouseLeave={() => (drag.current = null)}
        onMouseMove={(e) => {
          if (!drag.current) return;
          const dx = e.clientX - drag.current.x;
          const dy = e.clientY - drag.current.y;
          drag.current = { x: e.clientX, y: e.clientY };
          setView((v) => ({ ...v, x: v.x - dx / v.k, y: v.y - dy / v.k }));
        }}
      >
        <defs>
          <marker
            id="arrow"
            viewBox="0 0 10 10"
            refX="10"
            refY="5"
            markerWidth="6"
            markerHeight="6"
            orient="auto-start-reverse"
          >
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#52525b" />
          </marker>
        </defs>
        <g
          transform={`scale(${view.k}) translate(${-view.x} ${-view.y + height / 2 / view.k})`}
          data-testid="graph-root"
        >
          {edges.map((e, i) => {
            const a = layout.get(e.from);
            const b = layout.get(e.to);
            if (!a || !b) return null;
            const st = edgeStyle(e.rel);
            const op = Math.min(dim(e.from), dim(e.to)) * st.opacity;
            return (
              <g key={i} opacity={op}>
                <path
                  d={`M ${a.x + 150} ${a.y} C ${a.x + 185} ${a.y}, ${b.x - 35} ${b.y}, ${b.x} ${b.y}`}
                  fill="none"
                  stroke={st.stroke}
                  strokeWidth={1.2}
                  strokeDasharray={st.dash}
                  markerEnd="url(#arrow)"
                >
                  <title>{edgeTitle(e.rel)}</title>
                </path>
              </g>
            );
          })}
          {nodes.map((n) => {
            const p = layout.get(n.node);
            if (!p) return null;
            const isSel = selected === n.node;
            const isValue = n.type === 'instance' || n.type === 'artifact';
            const stroke =
              n.type === 'event'
                ? (STATUS_STROKE[n.status ?? 'UNKNOWN'] ?? '#71717a')
                : isValue
                  ? n.erased
                    ? '#f43f5e'
                    : '#0ea5e9'
                  : '#a78bfa';
            return (
              <g
                key={n.node}
                transform={`translate(${p.x} ${p.y})`}
                opacity={dim(n.node)}
                onClick={() => onSelect?.(n.node)}
                className="cursor-pointer"
              >
                {n.type === 'entity' ? (
                  <ellipse
                    cx={75}
                    cy={0}
                    rx={75}
                    ry={15}
                    fill="#18181b"
                    stroke={stroke}
                    strokeWidth={isSel ? 2.5 : 1}
                  />
                ) : (
                  <rect
                    x={0}
                    y={-15}
                    width={150}
                    height={30}
                    rx={isValue ? 10 : 3}
                    fill={isValue ? '#0c1a24' : '#18181b'}
                    stroke={isSel ? '#fafafa' : stroke}
                    strokeWidth={isSel ? 2 : 1}
                  />
                )}
                <text x={8} y={-2} fontSize={9} fill="#71717a" fontFamily="ui-monospace, monospace">
                  {n.type === 'event'
                    ? String(n.label).split(' ')[0].toLowerCase()
                    : isValue
                      ? `value${n.role ? ` · ${n.role}` : ''}${n.erased ? ' · erased' : ''}`
                      : n.type}
                </text>
                <text
                  x={8}
                  y={10}
                  fontSize={10.5}
                  fill="#e4e4e7"
                  fontFamily="ui-monospace, monospace"
                >
                  {(n.type === 'event'
                    ? String(n.label).split(' ').slice(1).join(' ')
                    : String(n.label)
                  ).slice(0, 22)}
                </text>
                <title>{`${n.label}\n${n.node}${n.actor ? `\nactor ${n.actor}` : ''}${
                  (n.same_content_instances ?? 0) > 1
                    ? `\nsame bytes as ${(n.same_content_instances ?? 1) - 1} other value(s) — still a distinct value`
                    : ''
                }`}</title>
              </g>
            );
          })}
        </g>
      </svg>
    </div>
  );
}
