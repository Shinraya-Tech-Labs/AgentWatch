'use client';

import { useEffect, useRef } from 'react';
import Image from 'next/image';
import gsap from 'gsap';
import ScrollTrigger from 'gsap/ScrollTrigger';
import Contributors from '../components/Contributors';
import CyberpunkIDCard from '../components/CyberpunkIDCard';

gsap.registerPlugin(ScrollTrigger);

// ─────────────────────────────────────────────
// Content
// ─────────────────────────────────────────────

const WHY_PARAGRAPHS = [
  'The monitoring gap in AI agents is well documented but unsolved.',
  'Gartner projects 40% of enterprise AI projects will be cancelled by 2027 — specifically due to monitoring gaps and inadequate risk controls.',
  '1 in 20 agent requests fail silently in production. The output looks correct. No error is thrown. You find out three hours later when a customer complains or a database is corrupted.',
  'Every existing tool — Langfuse, Arize Phoenix, Datadog — is post-hoc. They log what happened after it happened.',
  "AgentWatch was built to fix the architectural problem: an agent scoring its own reasoning is structurally biased toward overconfidence. A second independent model, with no access to the agent's reasoning trace, scores every step before the next action fires.",
  "Pre-execution. Not post-hoc. That's the difference.",
];

const DIFFERENTIATORS = [
  {
    title: 'Pre-execution blocking — not logging',
    desc: 'Every competitor logs after the fact. AgentWatch holds the action before it runs. Not an alert. A hard stop.',
  },
  {
    title: 'Independent reasoning auditor',
    desc: 'An agent scoring its own work is structurally biased. A separate model with no stake in the outcome catches what the agent misses — before the next action fires.',
  },
  {
    title: 'Git-backed rollback',
    desc: 'Every step is a checkpoint. Irreversible actions become reversible. One command back to any prior state.',
  },
  {
    title: 'Causal memory — not episodic storage',
    desc: 'Not just what happened but why. A temporal knowledge graph that answers: why did we choose X last week? Full reasoning trails across sessions.',
  },
  {
    title: 'OWASP Agentic Top 10 coverage',
    desc: 'All 10 attack vectors — prompt injection, tool abuse, goal hijacking, exfiltration — blocked pre-execution. No other tool has a complete test harness for all 10.',
  },
];

const NUMBERS = [
  { value: 143, suffix: '', label: 'Features built' },
  { value: 585, suffix: '', label: 'Tests passing' },
  { value: 9, suffix: '', label: 'Framework adapters' },
  { value: 64, suffix: '+', label: 'Safety patterns blocked' },
  { value: 12, suffix: '', label: 'Feature domains' },
  { value: 100, suffix: '%', label: 'Open source (Apache 2.0)' },
];

const TIMELINE = [
  {
    date: 'May 22, 2026',
    title: 'v0.1.0 — Initial Release',
    desc: 'Independent reasoning auditor, safety engine, live dashboard, git-backed rollback. 4 adapters. 47 tests. Zero marketing.',
  },
  {
    date: 'May 24, 2026',
    title: 'Community Forms',
    desc: '6 contributors showed up cold. 10 forks. No announcement, no marketing. The problem resonated immediately.',
  },
  {
    date: 'May 27, 2026',
    title: 'v0.2.0-preview — 90 Features',
    desc: '10 phases. 205 tests. Causal memory graph, multi-agent DAG, OWASP scanner, EU AI Act compliance, MCP server. 107 files changed.',
  },
  {
    date: 'May 28, 2026',
    title: 'PyPI + Landing Page',
    desc: 'agentwatch-ai live on PyPI. Landing page deployed. Community growing. 25+ open issues across all difficulty levels.',
  },
  {
    date: 'Next',
    title: 'The Road Ahead',
    desc: 'Production load testing at scale. Framework maintainer integrations. The open reasoning trace schema — becoming the OpenTelemetry of AI agents.',
  },
];

const FUTURE = [
  {
    title: 'Scale',
    desc: 'Load test at 500+ concurrent sessions. Benchmark AgentWatch overhead vs baseline. Production-battle-tested, not just preview.',
  },
  {
    title: 'Protocol',
    desc: 'Publish ReasoningTrace v1.0 as an open standard. Get LangGraph, AutoGen, Smolagents to adopt it natively. Become the OpenTelemetry of AI agent observability.',
  },
  {
    title: 'Platform',
    desc: 'AgentWatch Cloud — managed SaaS with team billing. Plugin marketplace. AgentWatch Intelligence — AI that watches your AI and surfaces patterns automatically.',
  },
];

// ─────────────────────────────────────────────
// Page
// ─────────────────────────────────────────────

export default function AboutPage() {
  const pageRef = useRef<HTMLDivElement>(null);
  const gridRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (prefersReduced) return;

    const ctx = gsap.context(() => {
      // Hero
      gsap.fromTo(
        '.about-hero-eyebrow, .about-hero-title, .about-hero-sub',
        { y: 30, opacity: 0 },
        {
          y: 0,
          opacity: 1,
          duration: 0.9,
          stagger: 0.15,
          ease: 'power3.out',
        },
      );
      gsap.fromTo(
        '.about-hero-underline',
        { scaleX: 0 },
        {
          scaleX: 1,
          transformOrigin: 'left center',
          duration: 1.1,
          delay: 0.6,
          ease: 'power3.out',
        },
      );

      // Common scroll fade-in for section headings
      gsap.utils.toArray<HTMLElement>('.about-section-title').forEach((el) => {
        gsap.fromTo(
          el,
          { y: 30, opacity: 0 },
          {
            y: 0,
            opacity: 1,
            duration: 0.8,
            ease: 'power3.out',
            scrollTrigger: { trigger: el, start: 'top 85%', once: true },
          },
        );
      });

      // Founder block
      gsap.fromTo(
        '.founder-avatar, .founder-text > *, .founder-badges > *',
        { y: 30, opacity: 0 },
        {
          y: 0,
          opacity: 1,
          duration: 0.7,
          stagger: 0.08,
          ease: 'power3.out',
          scrollTrigger: { trigger: '.founder-block', start: 'top 80%', once: true },
        },
      );

      // Why paragraphs
      gsap.fromTo(
        '.why-para',
        { y: 24, opacity: 0 },
        {
          y: 0,
          opacity: 1,
          duration: 0.7,
          stagger: 0.1,
          ease: 'power3.out',
          scrollTrigger: { trigger: '.why-block', start: 'top 80%', once: true },
        },
      );

      // Differentiators stagger
      gsap.set('.diff-card', { willChange: 'transform, opacity' });
      gsap.fromTo(
        '.diff-card',
        { y: 40, opacity: 0 },
        {
          y: 0,
          opacity: 1,
          duration: 0.7,
          stagger: 0.12,
          ease: 'power3.out',
          scrollTrigger: { trigger: '.diff-list', start: 'top 80%', once: true },
          onComplete: () => gsap.set('.diff-card', { clearProps: 'transform,opacity,willChange' }),
        },
      );

      // Numbers — stagger + counter
      gsap.fromTo(
        '.num-card',
        { y: 30, opacity: 0 },
        {
          y: 0,
          opacity: 1,
          duration: 0.7,
          stagger: 0.1,
          ease: 'power3.out',
          scrollTrigger: { trigger: gridRef.current, start: 'top 80%', once: true },
        },
      );

      // Timeline entries
      gsap.fromTo(
        '.timeline-entry',
        { x: -30, opacity: 0 },
        {
          x: 0,
          opacity: 1,
          duration: 0.7,
          stagger: 0.15,
          ease: 'power3.out',
          scrollTrigger: { trigger: '.timeline', start: 'top 80%', once: true },
        },
      );

      // Future cards
      gsap.set('.future-card', { willChange: 'transform, opacity' });
      gsap.fromTo(
        '.future-card',
        { y: 40, opacity: 0 },
        {
          y: 0,
          opacity: 1,
          duration: 0.7,
          stagger: 0.12,
          ease: 'power3.out',
          scrollTrigger: { trigger: '.future-grid', start: 'top 80%', once: true },
          onComplete: () =>
            gsap.set('.future-card', { clearProps: 'transform,opacity,willChange' }),
        },
      );

      // CTA
      gsap.fromTo(
        '.about-cta > *',
        { y: 30, opacity: 0 },
        {
          y: 0,
          opacity: 1,
          duration: 0.8,
          stagger: 0.1,
          ease: 'power3.out',
          scrollTrigger: { trigger: '.about-cta', start: 'top 85%', once: true },
        },
      );
    }, pageRef);

    // Cursor spotlight on all spotlight-cards within this page
    const onPointerMove = (e: PointerEvent) => {
      pageRef.current?.querySelectorAll<HTMLElement>('.spotlight-card').forEach((card) => {
        const r = card.getBoundingClientRect();
        const x = e.clientX - r.left;
        const y = e.clientY - r.top;
        if (x > -200 && x < r.width + 200 && y > -200 && y < r.height + 200) {
          card.style.setProperty('--mx', `${x}px`);
          card.style.setProperty('--my', `${y}px`);
        }
      });
    };
    pageRef.current?.addEventListener('pointermove', onPointerMove, { passive: true });

    setTimeout(() => ScrollTrigger.refresh(), 100);
    return () => {
      pageRef.current?.removeEventListener('pointermove', onPointerMove);
      ctx.revert();
    };
  }, []);

  return (
    <main
      ref={pageRef}
      className="relative min-h-screen bg-[#050505] text-[#ededed] overflow-hidden selection:bg-[#00f0ff]/30 selection:text-[#00f0ff]"
    >
      {/* Background Grid & Vignette */}
      <div
        className="absolute inset-0 z-0 pointer-events-none"
        style={{
          backgroundImage:
            'linear-gradient(rgba(255,255,255,0.03) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.03) 1px, transparent 1px)',
          backgroundSize: '64px 64px',
          backgroundPosition: 'center center',
          maskImage: 'radial-gradient(circle at center, black 20%, transparent 80%)',
          WebkitMaskImage: 'radial-gradient(circle at center, black 20%, transparent 80%)',
        }}
      />
      <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[800px] h-[500px] bg-[#00f0ff] rounded-full blur-[150px] opacity-[0.07] pointer-events-none z-0" />
      <div className="absolute inset-0 bg-[url('/noise.svg')] opacity-[0.05] pointer-events-none mix-blend-overlay z-0" />

      <div className="max-w-[900px] mx-auto px-6 pt-32 pb-24 relative z-10">
        {/* ─── 1. HERO ─── */}
        <section className="mb-24 text-center">
          <a
            href="/"
            className="about-hero-eyebrow group inline-flex items-center gap-2 px-3 py-1.5 rounded-full border border-[#e8ff47]/25 bg-[#e8ff47]/5 mb-6 hover:border-[#e8ff47]/60 hover:bg-[#e8ff47]/10 transition-all duration-200"
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              className="w-3.5 h-3.5 text-[#e8ff47] transition-transform duration-200 group-hover:-translate-x-0.5"
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 12H5M12 19l-7-7 7-7" />
            </svg>
            <span
              className="text-[10px] uppercase tracking-[0.2em] text-[#e8ff47]"
              style={{ fontFamily: 'var(--font-jetbrains)' }}
            >
              Back to Home
            </span>
          </a>
          <h1
            className="about-hero-title font-bold leading-[1.08] mb-5"
            style={{
              fontFamily: 'var(--font-syne)',
              fontSize: 'clamp(2rem, 5.2vw, 4rem)',
              background: 'linear-gradient(135deg, #ffffff 0%, #e8ff47 100%)',
              WebkitBackgroundClip: 'text',
              WebkitTextFillColor: 'transparent',
              backgroundClip: 'text',
              textWrap: 'balance',
            }}
          >
            About AgentWatch
          </h1>
          <div
            className="about-hero-underline mx-auto h-px mb-6"
            style={{
              width: '120px',
              background: 'linear-gradient(90deg, transparent, #e8ff47, transparent)',
            }}
          />
          <p
            className="about-hero-sub text-[#b8b8b8] max-w-xl mx-auto font-light"
            style={{ fontSize: 'clamp(1rem, 2vw, 1.125rem)' }}
          >
            Built by a developer who got tired of AI agents failing silently in production.
          </p>
        </section>

        {/* ─── 2. WHO BUILT IT ─── */}
        <section className="mb-24 founder-block">
          <h2
            className="about-section-title font-bold mb-12"
            style={{
              fontFamily: 'var(--font-syne)',
              fontSize: 'clamp(1.5rem, 3vw, 2.2rem)',
            }}
          >
            The <span className="gradient-text">Creators</span>
          </h2>

          <div className="flex flex-col gap-16 items-center">
            <CyberpunkIDCard
              creator={{
                name: 'Sreerevanth',
                role: 'Creator of AgentWatch',
                bio: "I'm a developer focused on AI systems, developer tools, open-source software, and building technology that solves real-world problems. I'm the creator of AgentWatch, an open-source observability and reasoning-auditing platform.",
                githubUrl: 'https://github.com/sreerevanth',
                githubUsername: 'sreerevanth',
                avatarUrl: 'https://github.com/sreerevanth.png',
                clearanceLevel: 'LEVEL 10',
                idNumber: 'AW-001',
                projects: ['AgentWatch', 'RepoPilot'],
                coreStack: [
                  'Python',
                  'TypeScript',
                  'ReactJS',
                  'NextJS',
                  'NodeJS',
                  'AI Agents',
                  'RAG',
                ],
                traits: ['AI Observability', 'Multi-Model Orchestration', 'Algo-Trading'],
                themeColor: '#e8ff47',
                accentColor: '#bcd20e',
              }}
            />

            <CyberpunkIDCard
              creator={{
                name: 'Shaurya Sanyal',
                role: 'Creator of Frontend & Landing Page',
                bio: 'I architected and built the entire frontend experience and landing page for AgentWatch. My goal was to create a brutalist, high-performance, and cyberpunk-inspired interface that perfectly matched the cutting-edge nature of the backend.',
                githubUrl: 'https://github.com/SHAURYASANYAL3',
                githubUsername: 'SHAURYASANYAL3',
                avatarUrl: 'https://github.com/SHAURYASANYAL3.png',
                clearanceLevel: 'LEVEL 09',
                idNumber: 'AW-002',
                projects: ['AgentWatch Dashboard', 'Landing Page', 'VoidSwift'],
                coreStack: ['Next.js', 'React', 'GSAP', 'Framer Motion', 'Tailwind'],
                traits: [
                  'Frontend Architecture',
                  'UI/UX Design',
                  'Motion Graphics',
                  'B.S. in Frontend Engineering',
                ],
                themeColor: '#00f0ff',
                accentColor: '#0077ff',
              }}
            />
          </div>
        </section>

        {/* ─── 3. WHY THIS EXISTS ─── */}
        <section className="mb-24 why-block">
          <h2
            className="about-section-title font-bold mb-8"
            style={{
              fontFamily: 'var(--font-syne)',
              fontSize: 'clamp(1.5rem, 3vw, 2.2rem)',
            }}
          >
            Why <span className="gradient-text">This Exists</span>
          </h2>
          <div className="space-y-5">
            {WHY_PARAGRAPHS.map((p, i) => (
              <p
                key={i}
                className="why-para text-[#c0c0c0] leading-[1.8]"
                style={{ fontSize: '1rem' }}
              >
                {p}
              </p>
            ))}
          </div>
        </section>

        {/* ─── 4. WHAT MAKES IT DIFFERENT ─── */}
        <section className="mb-24">
          <h2
            className="about-section-title font-bold mb-8"
            style={{
              fontFamily: 'var(--font-syne)',
              fontSize: 'clamp(1.5rem, 3vw, 2.2rem)',
            }}
          >
            What Makes It <span className="gradient-text">Different</span>
          </h2>
          <div className="diff-list space-y-4">
            {DIFFERENTIATORS.map((d, i) => (
              <div key={i} className="diff-card">
                <div className="spotlight-card p-6 sm:p-7">
                  <div className="spotlight-card-inner flex gap-5 items-start">
                    <span
                      className="flex-shrink-0 text-2xl sm:text-3xl font-bold leading-none"
                      style={{
                        color: '#e8ff47',
                        fontFamily: 'var(--font-syne)',
                      }}
                    >
                      {String(i + 1).padStart(2, '0')}
                    </span>
                    <div className="flex-1">
                      <h3
                        className="font-semibold text-[#e5e2e1] mb-2"
                        style={{
                          fontFamily: 'var(--font-syne)',
                          fontSize: '1.05rem',
                        }}
                      >
                        {d.title}
                      </h3>
                      <p className="text-sm text-[#b8b8b8] leading-relaxed">{d.desc}</p>
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* ─── 5. BY THE NUMBERS ─── */}
        <section className="mb-24">
          <h2
            className="about-section-title font-bold mb-8"
            style={{
              fontFamily: 'var(--font-syne)',
              fontSize: 'clamp(1.5rem, 3vw, 2.2rem)',
            }}
          >
            By <span className="gradient-text">The Numbers</span>
          </h2>
          <div ref={gridRef} className="num-grid grid grid-cols-2 md:grid-cols-3 gap-4">
            {NUMBERS.map((n, i) => (
              <div
                key={i}
                className="num-card dark-glass rounded-xl p-6 border-t-2 border-t-[#e8ff47]"
              >
                <div
                  className="font-bold mb-2"
                  style={{
                    fontFamily: 'var(--font-syne)',
                    fontSize: 'clamp(1.75rem, 3.5vw, 2.5rem)',
                    color: '#e8ff47',
                  }}
                >
                  <span className={`num-val-${i}`}>
                    {n.value}
                    {n.suffix}
                  </span>
                </div>
                <p className="text-[#b8b8b8] text-xs sm:text-sm leading-snug">{n.label}</p>
              </div>
            ))}
          </div>
        </section>

        {/* ─── 6. THE JOURNEY ─── */}
        <section className="mb-24">
          <h2
            className="about-section-title font-bold mb-8"
            style={{
              fontFamily: 'var(--font-syne)',
              fontSize: 'clamp(1.5rem, 3vw, 2.2rem)',
            }}
          >
            How It Was <span className="gradient-text">Built</span>
          </h2>
          <div className="timeline space-y-2">
            {TIMELINE.map((t, i) => (
              <div key={i} className="timeline-entry flex gap-4 sm:gap-6 items-start">
                <div
                  className="flex-shrink-0 flex flex-col items-center"
                  style={{ minWidth: '110px' }}
                >
                  <div
                    className="px-3 py-1.5 rounded-md text-xs font-medium text-center"
                    style={{
                      background: 'rgba(232,255,71,0.08)',
                      border: '1px solid rgba(232,255,71,0.3)',
                      color: '#e8ff47',
                      fontFamily: 'var(--font-jetbrains)',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {t.date}
                  </div>
                  {i < TIMELINE.length - 1 && (
                    <div
                      className="w-px flex-1 mt-2"
                      style={{
                        minHeight: '60px',
                        background:
                          'linear-gradient(180deg, rgba(232,255,71,0.4) 0%, rgba(232,255,71,0.05) 100%)',
                      }}
                    />
                  )}
                </div>
                <div className="flex-1 pb-8">
                  <h3
                    className="font-semibold text-[#e5e2e1] mb-2"
                    style={{
                      fontFamily: 'var(--font-syne)',
                      fontSize: '1.05rem',
                    }}
                  >
                    {t.title}
                  </h3>
                  <p className="text-sm text-[#b8b8b8] leading-relaxed">{t.desc}</p>
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* ─── 7. FUTURE SCOPE ─── */}
        <section className="mb-24">
          <h2
            className="about-section-title font-bold mb-8"
            style={{
              fontFamily: 'var(--font-syne)',
              fontSize: 'clamp(1.5rem, 3vw, 2.2rem)',
            }}
          >
            Where This <span className="gradient-text">Is Going</span>
          </h2>
          <div className="future-grid grid grid-cols-1 md:grid-cols-3 gap-4">
            {FUTURE.map((f, i) => (
              <div key={i} className="future-card">
                <div className="spotlight-card p-6 h-full">
                  <div className="spotlight-card-inner h-full flex flex-col">
                    <div
                      className="text-[10px] uppercase tracking-[0.2em] mb-3"
                      style={{
                        color: '#e8ff47',
                        fontFamily: 'var(--font-jetbrains)',
                      }}
                    >
                      {String(i + 1).padStart(2, '0')} / 03
                    </div>
                    <h3
                      className="font-semibold text-[#e5e2e1] mb-3"
                      style={{
                        fontFamily: 'var(--font-syne)',
                        fontSize: '1.15rem',
                      }}
                    >
                      {f.title}
                    </h3>
                    <p className="text-sm text-[#b8b8b8] leading-relaxed">{f.desc}</p>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>
      </div>

      {/* ─── 8. CONTRIBUTORS ─── (full-width, reuses homepage component) */}
      <Contributors />

      {/* ─── 9. CTA ─── */}
      <section className="py-20 px-6">
        <div className="max-w-[900px] mx-auto about-cta flex flex-col items-center text-center gap-6">
          <h2
            className="font-bold"
            style={{
              fontFamily: 'var(--font-syne)',
              fontSize: 'clamp(1.4rem, 3vw, 2rem)',
            }}
          >
            Join the <span className="gradient-text">community.</span>
          </h2>
          <div className="flex flex-wrap items-center justify-center gap-4">
            <a
              href="https://github.com/sreerevanth/agentwatch"
              target="_blank"
              rel="noreferrer"
              className="btn-magnetic px-6 py-3 rounded-lg border border-white/20 text-[#e5e2e1] hover:border-[#e8ff47]/60 hover:text-[#e8ff47] transition-all duration-200 text-sm font-medium"
            >
              View on GitHub →
            </a>
            <a
              href="https://discord.gg/UT9uaeY46e"
              target="_blank"
              rel="noreferrer"
              className="btn-magnetic btn-discord-pulse px-6 py-3 rounded-lg bg-[#5865F2] hover:bg-[#4752c4] text-white transition-all duration-200 text-sm font-medium"
            >
              Join Discord →
            </a>
          </div>
          <p className="text-xs text-[#8a8a8a]" style={{ fontFamily: 'var(--font-jetbrains)' }}>
            Apache 2.0 · Free forever · Built in Bangalore 🇮🇳
          </p>
        </div>
      </section>
    </main>
  );
}
