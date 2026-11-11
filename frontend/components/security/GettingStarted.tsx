import { useRef, useState, type KeyboardEvent } from 'react';

/**
 * First-run screen: how to connect an agent, and a live status that flips to the normal home
 * screen by itself once the first decision arrives (the status query keeps polling).
 */

interface Setup {
  id: string;
  label: string;
  intro: string;
  steps: { text: string; code?: string }[];
  note: string;
}

const SETUPS: Setup[] = [
  {
    id: 'claude',
    label: 'Claude Code',
    intro: 'A PreToolUse hook: every tool call Claude Code proposes is decided before it runs.',
    steps: [
      {
        text: 'In your project folder, install the hook:',
        code: 'panoptis protect claude-hook install --on-failure-block',
      },
      { text: 'Start Claude Code in that folder. Its next tool call appears here.' },
    ],
    note: 'The hook goes into ./.claude/settings.local.json. --on-failure-block needs Claude Code 2.1.295 or later; on older versions a hook that fails to start lets the tool run.',
  },
  {
    id: 'mcp',
    label: 'Cursor / MCP',
    intro:
      'The MCP gateway sits between your client and an MCP server and checks every tools/call.',
    steps: [
      {
        text: 'Print a config entry that runs your server behind the gateway (replace everything after -- with the server you already use):',
        code: 'panoptis protect mcp-gateway config --server-name filesystem -- npx -y @modelcontextprotocol/server-filesystem .',
      },
      {
        text: 'Paste the printed mcpServers entry into .cursor/mcp.json (or your client’s MCP config) and restart the client.',
      },
    ],
    note: 'Only clients that start the gateway are covered; a server the client launches directly bypasses it.',
  },
  {
    id: 'python',
    label: 'Python agent',
    intro: 'Wrap the functions your agent calls as tools. Blocked calls never run.',
    steps: [
      {
        text: 'Report to this dashboard and protect a tool:',
        code: `from panoptis.protect import protect_tool, use_local_dashboard

use_local_dashboard()  # record decisions where \`panoptis up\` reads them

@protect_tool
def run_shell(command: str) -> str:
    ...`,
      },
      {
        text: 'OpenAI Agents SDK: protect every function tool of an agent instead:',
        code: `from panoptis.protect.integrations import protect_openai_agent

protect_openai_agent(agent)`,
      },
    ],
    note: 'LangChain, LangGraph, AutoGen and smolagents have integrations too: see docs/ADAPTER_SUPPORT.md.',
  },
];

function CopyBlock({ code }: { code: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };
  return (
    <div className="relative">
      <pre className="overflow-x-auto rounded-xl border border-white/10 bg-black/50 p-3 pr-20 font-mono text-[13px] leading-relaxed text-emerald-100">
        {code}
      </pre>
      <button
        type="button"
        onClick={copy}
        className="absolute right-2 top-2 rounded-lg border border-white/20 bg-white/10 px-2.5 py-1 text-xs text-zinc-50 hover:bg-white/20"
      >
        {copied ? 'Copied' : 'Copy'}
      </button>
    </div>
  );
}

export function GettingStarted() {
  const [tab, setTab] = useState(SETUPS[0].id);
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});
  const current = SETUPS.find((s) => s.id === tab) ?? SETUPS[0];
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const i = SETUPS.findIndex((s) => s.id === tab);
    const next =
      e.key === 'ArrowRight'
        ? SETUPS[(i + 1) % SETUPS.length]
        : e.key === 'ArrowLeft'
          ? SETUPS[(i - 1 + SETUPS.length) % SETUPS.length]
          : null;
    if (next) {
      e.preventDefault();
      setTab(next.id);
      refs.current[next.id]?.focus();
    }
  };
  return (
    <section aria-labelledby="gs-title" className="glass-strong p-5 sm:p-7">
      <p className="text-xs font-semibold uppercase tracking-[0.2em] text-zinc-200">
        Getting started
      </p>
      <h1 id="gs-title" className="mt-2 text-2xl font-semibold text-zinc-50 sm:text-3xl">
        Connect your first agent
      </h1>
      <p className="mt-1.5 max-w-3xl text-sm text-zinc-200 sm:text-base">
        Nothing has reported to this workspace yet. Pick how your agent runs, follow the steps, and
        its first tool call shows up here on its own.
      </p>
      <p
        role="status"
        className="mt-4 inline-flex items-center gap-2 rounded-full border border-sky-300/40 bg-sky-400/10 px-3 py-1.5 text-sm text-sky-50"
      >
        <span className="relative flex h-2.5 w-2.5">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-sky-300 opacity-75" />
          <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-sky-300" />
        </span>
        Waiting for your first agent session…
      </p>

      <div
        role="tablist"
        aria-label="How your agent runs"
        onKeyDown={onKey}
        className="mt-6 flex flex-wrap gap-2"
      >
        {SETUPS.map((s) => (
          <button
            key={s.id}
            ref={(el) => {
              refs.current[s.id] = el;
            }}
            id={`gs-tab-${s.id}`}
            type="button"
            role="tab"
            aria-selected={tab === s.id}
            aria-controls={`gs-panel-${s.id}`}
            tabIndex={tab === s.id ? 0 : -1}
            onClick={() => setTab(s.id)}
            className={`rounded-lg px-3.5 py-2 text-sm ${tab === s.id ? 'bg-white/20 text-zinc-50' : 'border border-white/15 text-zinc-200 hover:bg-white/10'}`}
          >
            {s.label}
          </button>
        ))}
      </div>
      <div
        role="tabpanel"
        id={`gs-panel-${current.id}`}
        aria-labelledby={`gs-tab-${current.id}`}
        className="mt-4 space-y-3"
      >
        <p className="text-sm text-zinc-100">{current.intro}</p>
        <ol className="space-y-3">
          {current.steps.map((step, i) => (
            <li key={step.text} className="space-y-2">
              <p className="text-sm text-zinc-200">
                <span className="mr-2 font-mono text-zinc-400">{i + 1}.</span>
                {step.text}
              </p>
              {step.code ? <CopyBlock code={step.code} /> : null}
            </li>
          ))}
        </ol>
        <p className="text-xs text-zinc-300">{current.note}</p>
      </div>
      <p className="mt-6 border-t border-white/10 pt-4 text-sm text-zinc-300">
        Want to look around first? <code className="text-zinc-100">panoptis demo</code> opens a
        separate demo workspace with sample data;{' '}
        <code className="text-zinc-100">panoptis demo --clear</code> removes it. Nothing from it
        appears here.
      </p>
    </section>
  );
}
