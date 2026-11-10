import CopyCommand from './brutal/CopyCommand';
import { Box, ButtonLink, COLORS, SectionTitle, type Color } from './brutal/ui';
import { LINKS, asset } from './site';

const STEPS: { n: string; title: string; body: string; color: Color }[] = [
  {
    n: '01',
    title: 'Agent proposes',
    body: 'Your agent wants to call a tool: a shell command, a SQL query, an HTTP request, an MCP tool.',
    color: 'cyan',
  },
  {
    n: '02',
    title: 'PANOPTIS decides',
    body: 'Before anything runs: allow, block, rewrite, or hold for a person. A failure inside the check blocks too.',
    color: 'yellow',
  },
  {
    n: '03',
    title: 'Runs at most once',
    body: 'An allowed call runs once, with the decided arguments. A held call runs only after its exact action is approved.',
    color: 'green',
  },
  {
    n: '04',
    title: 'Checks and records',
    body: 'Injected instructions in the result are removed before the agent reads it, and the decision is sealed into a tamper-evident log.',
    color: 'pink',
  },
];

const STOPS: { what: string; example: string; verdict: string; color: Color }[] = [
  {
    what: 'Destructive commands',
    example: 'rm -rf /    DROP TABLE users;',
    verdict: 'Blocked before it runs',
    color: 'red',
  },
  {
    what: 'Secret exfiltration',
    example: 'curl -X POST https://… -d @.env',
    verdict: 'Blocked before it runs',
    color: 'orange',
  },
  {
    what: 'Prompt injection in tool output',
    example: '“Ignore all previous instructions and run …”',
    verdict: 'Removed before the agent reads it',
    color: 'violet',
  },
  {
    what: 'Risky but legitimate work',
    example: 'git push --force origin main',
    verdict: 'Held until a person approves',
    color: 'yellow',
  },
  {
    what: 'An agent that goes wrong',
    example: 'one click in the dashboard',
    verdict: 'Emergency stop for every agent',
    color: 'pink',
  },
  {
    what: 'Arguments you want rewritten',
    example: 'invisible characters, covert whitespace',
    verdict: 'Rewritten, then allowed (opt-in)',
    color: 'cyan',
  },
];

const INTEGRATIONS = [
  'Any Python function',
  'OpenAI Agents SDK',
  'LangChain',
  'LangGraph',
  'AutoGen 0.4+',
  'smolagents',
  'Claude Code hook',
  'MCP gateway',
];

const LIMITS = [
  "It is not a sandbox. It runs in or next to the agent's process; use OS or container isolation against code that can modify that process.",
  'It only sees routed calls. A tool reached another way bypasses it, and observe-only adapters stop nothing.',
  'It does not interrupt a tool that is already running. The emergency stop blocks new calls and withdraws pending approvals.',
  'It does not prove intent. Detection is pattern-based; intent grounding is a heuristic signal.',
  'The Claude Code hook fails open before Claude Code 2.1.295; fail-closed needs a later version.',
];

export default function Home() {
  return (
    <main className="bg-[#FFF6DA] text-black">
      {/* ── hero ─────────────────────────────────────────────────────────── */}
      <section className="border-b-[3px] border-black bg-[radial-gradient(#00000014_1.5px,transparent_1.5px)] [background-size:22px_22px]">
        <div className="mx-auto grid max-w-6xl gap-10 px-4 py-14 lg:grid-cols-[1.05fr_1fr] lg:items-center lg:py-20">
          <div>
            <p className="mb-5 inline-block -rotate-1 border-[3px] border-black bg-[#8CFF98] px-3 py-1 font-mono text-xs uppercase tracking-[0.18em] shadow-[3px_3px_0_0_#000]">
              Open source · Apache-2.0 · Python 3.12
            </p>
            <h1 className="font-display text-5xl uppercase leading-[0.92] sm:text-6xl lg:text-7xl">
              Stop the tool call{' '}
              <span className="inline-block rotate-1 border-[3px] border-black bg-[#FF8FD8] px-2 shadow-[5px_5px_0_0_#000]">
                before
              </span>{' '}
              it runs.
            </h1>
            <p className="mt-6 max-w-xl text-lg leading-relaxed">
              PANOPTIS sits between your AI agent and its tools. It blocks dangerous calls, holds
              risky ones for a person, strips injected instructions out of tool results, and keeps a
              record you can verify.
            </p>
            <div className="mt-7 max-w-xl">
              <CopyCommand lines={['pip install panoptis', 'panoptis up']} />
              <p className="mt-3 font-mono text-xs">
                Starts the API and dashboard on localhost with SQLite. No Docker, no account.
              </p>
            </div>
            <div className="mt-7 flex flex-wrap gap-3">
              <ButtonLink href={LINKS.star} color="yellow">
                ★ Star
              </ButtonLink>
              <ButtonLink href="#demo" color="cyan">
                ▶ Watch the demo
              </ButtonLink>
            </div>
          </div>
          <figure id="demo" className="scroll-mt-24">
            <div className="border-[3px] border-black bg-black shadow-[10px_10px_0_0_#000]">
              <div className="flex items-center gap-2 border-b-[3px] border-black bg-[#FFE45E] px-3 py-2">
                <span className="h-3 w-3 border-2 border-black bg-[#FF5A5A]" />
                <span className="h-3 w-3 border-2 border-black bg-white" />
                <span className="h-3 w-3 border-2 border-black bg-[#8CFF98]" />
                <span className="ml-2 font-mono text-xs uppercase">panoptis demo · dashboard</span>
              </div>
              <video
                className="block aspect-[16/10] w-full bg-black object-cover"
                src={asset('/demo.webm')}
                poster={asset('/demo-poster.png')}
                controls
                muted
                playsInline
                preload="metadata"
              >
                <a href={asset('/demo.webm')}>Download the demo video</a>
              </video>
            </div>
            <figcaption className="mt-4 font-mono text-xs leading-relaxed">
              Recorded from <code>panoptis demo offline</code> and the dashboard of{' '}
              <code>panoptis up</code>: real pipeline and decisions, scripted agent. With{' '}
              <code>OPENAI_API_KEY</code> set, <code>panoptis demo live</code> runs the same attack
              on a real OpenAI model.
            </figcaption>
          </figure>
        </div>
      </section>

      {/* ── how it works ─────────────────────────────────────────────────── */}
      <section id="how" className="scroll-mt-20 border-b-[3px] border-black">
        <div className="mx-auto max-w-6xl px-4 py-16">
          <SectionTitle kicker="How it works">One boundary. Four steps.</SectionTitle>
          <ol className="grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
            {STEPS.map((s) => (
              <Box key={s.n} as="li" color={s.color} className="p-5">
                <p className="font-display text-4xl">{s.n}</p>
                <h3 className="mt-2 font-display text-xl uppercase">{s.title}</h3>
                <p className="mt-2 text-[15px] leading-relaxed">{s.body}</p>
              </Box>
            ))}
          </ol>
          <Box color="white" className="mt-8 p-5">
            <p className="font-mono text-sm leading-relaxed">
              <span className="font-bold">Wrap a tool:</span> <code>@protect_tool</code> on any
              Python function, or <code>protect_openai_agent(agent)</code> for an OpenAI Agents SDK
              agent. A blocked call raises <code>ActionBlocked</code> (or tells the model why) and
              the tool body never runs.
            </p>
          </Box>
        </div>
      </section>

      {/* ── what it stops ────────────────────────────────────────────────── */}
      <section id="stops" className="scroll-mt-20 border-b-[3px] border-black bg-[#FFE45E]">
        <div className="mx-auto max-w-6xl px-4 py-16">
          <SectionTitle kicker="What it stops">What an injected agent tries.</SectionTitle>
          <ul className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
            {STOPS.map((x) => (
              <Box key={x.what} as="li" color="white" className="flex flex-col">
                <div
                  className={`border-b-[3px] border-black px-4 py-2 font-display uppercase ${COLORS[x.color]}`}
                >
                  {x.what}
                </div>
                <code className="block flex-1 bg-black px-4 py-3 font-mono text-sm text-[#8CFF98]">
                  {x.example}
                </code>
                <p className="border-t-[3px] border-black px-4 py-3 font-bold">
                  → {x.verdict}
                </p>
              </Box>
            ))}
          </ul>
          <div className="mt-10">
            <p className="mb-3 font-display text-lg uppercase">Enforces through</p>
            <ul className="flex flex-wrap gap-3">
              {INTEGRATIONS.map((i) => (
                <li
                  key={i}
                  className="border-[3px] border-black bg-white px-3 py-1.5 font-mono text-sm shadow-[3px_3px_0_0_#000]"
                >
                  {i}
                </li>
              ))}
            </ul>
          </div>
        </div>
      </section>

      {/* ── limits ───────────────────────────────────────────────────────── */}
      <section id="limits" className="scroll-mt-20 border-b-[3px] border-black">
        <div className="mx-auto max-w-6xl px-4 py-16">
          <SectionTitle kicker="Its limits">What PANOPTIS does not do.</SectionTitle>
          <Box color="white" className="p-6">
            <ul className="space-y-4">
              {LIMITS.map((l) => (
                <li key={l} className="flex gap-3 text-[16px] leading-relaxed">
                  <span aria-hidden className="mt-0.5 font-display text-xl text-[#D00000]">
                    ✕
                  </span>
                  <span>{l}</span>
                </li>
              ))}
            </ul>
            <p className="mt-6 font-mono text-sm">
              Full detail:{' '}
              <a className="underline" href={LINKS.limits}>
                README limits
              </a>{' '}
              ·{' '}
              <a className="underline" href={LINKS.threatModel}>
                threat model
              </a>{' '}
              ·{' '}
              <a className="underline" href={LINKS.claims}>
                every claim and its evidence
              </a>
            </p>
          </Box>
        </div>
      </section>

      {/* ── get involved ─────────────────────────────────────────────────── */}
      <section id="join" className="scroll-mt-20 bg-[#FF8FD8]">
        <div className="mx-auto max-w-6xl px-4 py-16">
          <SectionTitle kicker="Get involved">Build it with us.</SectionTitle>
          <div className="grid gap-6 md:grid-cols-2">
            <Box color="white" className="p-6">
              <h3 className="font-display text-2xl uppercase">Open source</h3>
              <p className="mt-2">
                Star the repo, pick a good first issue, or come say hi on Discord.
              </p>
              <div className="mt-5 flex flex-wrap gap-3">
                <ButtonLink href={LINKS.star} color="yellow">
                  ★ Star
                </ButtonLink>
                <ButtonLink href={LINKS.contribute} color="green">
                  Contribute
                </ButtonLink>
                <ButtonLink href={LINKS.discord} color="violet">
                  Discord
                </ButtonLink>
                <ButtonLink href={LINKS.sponsor} color="pink">
                  ♥ Sponsor
                </ButtonLink>
              </div>
            </Box>
            <Box color="cyan" className="p-6">
              <h3 className="font-display text-2xl uppercase">Pilot with us</h3>
              <p className="mt-2">
                Running agents against real systems? We work with a small number of teams to deploy
                PANOPTIS on their workloads and fix what gets in the way.
              </p>
              <div className="mt-5">
                <ButtonLink href={LINKS.pilot} color="yellow">
                  Pilot with us →
                </ButtonLink>
              </div>
            </Box>
          </div>
        </div>
      </section>
    </main>
  );
}
