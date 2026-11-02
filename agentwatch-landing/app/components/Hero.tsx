'use client';

import { useEffect, useRef, useState } from 'react';
import gsap from 'gsap';

// Note: the neural canvas is rendered once site-wide from page.tsx; the hero
// just frames it with a vignette and content overlay.

const TELEMETRY = [
  { text: 'monitoring reasoning…', tone: 'neutral' },
  { text: 'checkpoint created · step 47', tone: 'ok' },
  { text: 'confidence dropped to 0.21', tone: 'warn' },
  { text: 'unsafe tool call blocked', tone: 'bad' },
  { text: 'DAG trace · 12 spans', tone: 'neutral' },
  { text: 'replay buffer · 4.2MB', tone: 'ok' },
];

export default function Hero() {
  const badgeRef = useRef<HTMLDivElement>(null);
  const headlineRef = useRef<HTMLHeadingElement>(null);
  const subRef = useRef<HTMLParagraphElement>(null);
  const terminalRef = useRef<HTMLDivElement>(null);
  const ctaRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const blockedRef = useRef<HTMLDivElement>(null);
  const telemetryRef = useRef<HTMLDivElement>(null);
  const [copied, setCopied] = useState(false);
  const [telIdx, setTelIdx] = useState(0);

  const CMD = 'pip install agentwatch-ai';

  useEffect(() => {
    const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const isMobile = window.innerWidth < 768;
    if (prefersReduced || isMobile) return; // Skip heavy JS intro on mobile for instant LCP

    const tl = gsap.timeline({ delay: 0.4 });
    const targets = [
      badgeRef.current,
      headlineRef.current,
      subRef.current,
      terminalRef.current,
      ctaRef.current,
      scrollRef.current,
    ];

    targets.forEach((el) => {
      if (el) gsap.set(el, { y: 40, opacity: 0, willChange: 'transform, opacity' });
    });

    targets.forEach((el, i) => {
      if (el) {
        tl.to(
          el,
          {
            y: 0,
            opacity: 1,
            duration: 0.9,
            ease: 'power3.out',
            onComplete: () => gsap.set(el, { clearProps: 'willChange' }),
          },
          i * 0.12,
        );
      }
    });

    // Reveal floating panels after hero text settles
    if (blockedRef.current) {
      gsap.fromTo(
        blockedRef.current,
        { x: -30, opacity: 0, willChange: 'transform, opacity' },
        { x: 0, opacity: 1, duration: 1.1, ease: 'power3.out', delay: 1.6 },
      );
    }
    if (telemetryRef.current) {
      gsap.fromTo(
        telemetryRef.current,
        { x: 30, opacity: 0, willChange: 'transform, opacity' },
        { x: 0, opacity: 1, duration: 1.1, ease: 'power3.out', delay: 1.8 },
      );
    }

    // Cycle telemetry messages
    const interval = setInterval(() => {
      setTelIdx((v) => (v + 1) % TELEMETRY.length);
    }, 2800);

    return () => clearInterval(interval);
  }, []);

  // Magnetic button effect — runs once after mount
  useEffect(() => {
    const onMove = (e: PointerEvent) => {
      document.querySelectorAll<HTMLElement>('.btn-magnetic').forEach((btn) => {
        const r = btn.getBoundingClientRect();
        const x = e.clientX - r.left;
        const y = e.clientY - r.top;
        if (x > -100 && x < r.width + 100 && y > -100 && y < r.height + 100) {
          btn.style.setProperty('--mx', `${x}px`);
          btn.style.setProperty('--my', `${y}px`);
        }
      });
    };
    window.addEventListener('pointermove', onMove, { passive: true });
    return () => window.removeEventListener('pointermove', onMove);
  }, []);

  const handleCopy = () => {
    navigator.clipboard.writeText(CMD).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  const tel = TELEMETRY[telIdx];

  return (
    <section className="relative min-h-[700px] h-screen flex flex-col items-center justify-center overflow-hidden">
      {/* Vignette (the neural canvas is mounted once at the page root) */}
      <div
        className="absolute inset-0 z-[1] pointer-events-none"
        style={{
          background:
            'radial-gradient(ellipse at center, transparent 0%, rgba(10,10,10,0.6) 60%, #0a0a0a 95%)',
        }}
      />

      {/* — Floating Blocked Action panel (left) —
          Outer wrapper handles vertical centering so its transform
          never collides with GSAP's x/opacity animation on the inner. */}
      <div
        className="hidden lg:block absolute z-10 left-8 xl:left-12 top-1/2 -translate-y-1/2"
        style={{ width: '270px' }}
      >
        <div ref={blockedRef} className="opacity-0">
          <div className="relative rounded-xl overflow-hidden border border-[#00f0ff]/30 bg-[#0c0c0c]/92 backdrop-blur-2xl shadow-[0_0_50px_rgba(0,240,255,0.15)]">
            {/* Scan sweep */}
            <div
              className="absolute inset-0 pointer-events-none opacity-40"
              style={{
                background:
                  'linear-gradient(90deg, transparent, rgba(0,240,255,0.15), transparent)',
                animation: 'scan-sweep 4s linear infinite',
                width: '30%',
              }}
            />
            <div className="px-4 py-3 border-b border-white/5 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-xs text-[#ff6b6b]">⚠</span>
                <span
                  className="text-[10px] uppercase tracking-[0.2em] text-[#ff6b6b]"
                  style={{ fontFamily: 'var(--font-jetbrains)' }}
                >
                  Blocked Action
                </span>
              </div>
              <span className="w-1.5 h-1.5 rounded-full bg-[#ff6b6b] live-dot" />
            </div>
            <div className="p-4 space-y-3">
              <code
                className="block text-xs text-[#e5e2e1] bg-black/40 rounded px-2 py-1.5 border border-white/5"
                style={{ fontFamily: 'var(--font-jetbrains)' }}
              >
                rm -rf /tmp/*
              </code>

              <div>
                <div
                  className="text-[10px] uppercase tracking-[0.15em] text-[#555] mb-1"
                  style={{ fontFamily: 'var(--font-jetbrains)' }}
                >
                  reason
                </div>
                <p className="text-xs text-[#c5c5c5] leading-snug">
                  Recursive deletion outside approved workspace.
                </p>
              </div>

              <div className="grid grid-cols-2 gap-3 pt-2 border-t border-white/5">
                <div>
                  <div
                    className="text-[10px] uppercase tracking-[0.15em] text-[#555]"
                    style={{ fontFamily: 'var(--font-jetbrains)' }}
                  >
                    confidence
                  </div>
                  <div
                    className="text-sm text-[#e8ff47] mt-0.5"
                    style={{ fontFamily: 'var(--font-jetbrains)' }}
                  >
                    0.18
                  </div>
                </div>
                <div>
                  <div
                    className="text-[10px] uppercase tracking-[0.15em] text-[#555]"
                    style={{ fontFamily: 'var(--font-jetbrains)' }}
                  >
                    blast radius
                  </div>
                  <div
                    className="text-sm text-[#ff6b6b] mt-0.5"
                    style={{ fontFamily: 'var(--font-jetbrains)' }}
                  >
                    HIGH
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
      {/* /blocked wrapper */}

      {/* — Floating telemetry chip (right) — */}
      <div
        className="hidden lg:block absolute z-10 right-8 xl:right-12 top-1/2 -translate-y-1/2"
        style={{ width: '240px' }}
      >
        <div ref={telemetryRef} className="opacity-0">
          <div className="rounded-xl border border-white/20 bg-[#0c0c0c]/80 backdrop-blur-2xl px-4 py-3 shadow-[0_0_50px_rgba(232,255,71,0.1)]">
            <div className="flex items-center justify-between mb-2">
              <span
                className="text-[10px] uppercase tracking-[0.2em] text-[#888]"
                style={{ fontFamily: 'var(--font-jetbrains)' }}
              >
                Live Telemetry
              </span>
              <span className="w-1.5 h-1.5 rounded-full bg-[#00f0ff] live-dot shadow-[0_0_10px_#00f0ff]" />
            </div>
            <div key={telIdx} style={{ animation: 'telemetry-float 2.8s ease both' }}>
              <div className="flex items-start gap-2">
                <span
                  className="text-xs mt-0.5"
                  style={{
                    color:
                      tel.tone === 'bad'
                        ? '#ff6b6b'
                        : tel.tone === 'warn'
                          ? '#ffb84d'
                          : tel.tone === 'ok'
                            ? '#e8ff47'
                            : '#888',
                    fontFamily: 'var(--font-jetbrains)',
                  }}
                >
                  ▸
                </span>
                <span
                  className="text-xs text-[#e5e2e1] leading-snug"
                  style={{ fontFamily: 'var(--font-jetbrains)' }}
                >
                  {tel.text}
                </span>
              </div>
            </div>
            {/* Trace bar */}
            <div className="mt-3 h-px w-full bg-white/5 overflow-hidden">
              <div
                className="h-full bg-gradient-to-r from-transparent via-[#e8ff47] to-transparent"
                style={{ width: '40%', animation: 'scan-sweep 3s linear infinite' }}
              />
            </div>
          </div>
        </div>
      </div>
      {/* /telemetry wrapper */}

      {/* Content */}
      <div className="relative z-10 flex flex-col items-center text-center px-6 max-w-4xl mx-auto gap-6">
        <div
          ref={badgeRef}
          className="flex items-center gap-2 px-3 py-1.5 rounded-full border border-white/10 bg-white/5 backdrop-blur-sm"
        >
          <span className="w-2 h-2 rounded-full bg-green-400 animate-green-pulse" />
          <span className="text-sm text-[#888]">Now in Beta</span>
        </div>

        <h1
          ref={headlineRef}
          className="font-bold leading-[1.05]"
          style={{
            fontFamily: 'var(--font-syne)',
            fontSize: 'clamp(2rem, 5.2vw, 4rem)',
            textWrap: 'balance',
          }}
        >
          Your AI agent is lying to you.
          <br />
          <span className="gradient-text">AgentWatch catches it.</span>
        </h1>

        <p
          ref={subRef}
          className="text-[#b8b8b8] max-w-xl font-light"
          style={{ fontSize: 'clamp(1rem, 2vw, 1.125rem)' }}
        >
          Pre-execution blocking for AI agents.
          <br />
          Not post-hoc logging.
        </p>

        <div
          ref={terminalRef}
          className="flex items-center gap-3 rounded-lg px-4 py-3"
          style={{
            background: 'rgba(15,15,15,0.92)',
            backdropFilter: 'blur(14px)',
            WebkitBackdropFilter: 'blur(14px)',
            border: '1px solid rgba(255,255,255,0.08)',
          }}
        >
          <span className="text-[#e8ff47]" style={{ fontFamily: 'var(--font-jetbrains)' }}>
            $
          </span>
          <code
            className="text-[#e5e2e1] text-sm sm:text-base select-all"
            style={{ fontFamily: 'var(--font-jetbrains)' }}
          >
            {CMD}
          </code>
          <button
            onClick={handleCopy}
            className="ml-2 text-[#888] hover:text-[#e8ff47] transition-colors duration-200 flex-shrink-0"
            title="Copy"
          >
            {copied ? (
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                className="w-4 h-4 text-green-400"
              >
                <polyline points="20 6 9 17 4 12" />
              </svg>
            ) : (
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                className="w-4 h-4"
              >
                <rect x="9" y="9" width="13" height="13" rx="2" />
                <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1" />
              </svg>
            )}
          </button>
        </div>

        {/* CTA Buttons */}
        <div ref={ctaRef} className="flex flex-wrap items-center justify-center gap-4">
          <a
            href="https://github.com/sreerevanth/agentwatch"
            target="_blank"
            rel="noreferrer"
            className="btn-magnetic flex items-center gap-2 px-6 py-3 rounded-lg bg-gradient-to-r from-[#00f0ff] to-[#e8ff47] text-[#0a0a0a] font-bold text-sm hover:from-[#00d0dd] hover:to-[#bcd20e] transition-all"
            style={{ boxShadow: '0 0 0 1px rgba(0,240,255,0.6), 0 0 25px rgba(0,240,255,0.3)' }}
          >
            <svg viewBox="0 0 24 24" fill="currentColor" className="w-4 h-4">
              <path d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z" />
            </svg>
            Star on GitHub
          </a>
          <a
            href="https://discord.gg/UT9uaeY46e"
            target="_blank"
            rel="noreferrer"
            className="btn-magnetic btn-discord-pulse flex items-center gap-2 px-6 py-3 rounded-lg bg-[#5865F2] hover:bg-[#4752c4] text-white transition-colors duration-200 text-sm font-medium"
          >
            <svg viewBox="0 0 24 24" fill="currentColor" className="w-4 h-4">
              <path d="M20.317 4.37a19.791 19.791 0 00-4.885-1.515.074.074 0 00-.079.037c-.21.375-.444.864-.608 1.25a18.27 18.27 0 00-5.487 0 12.64 12.64 0 00-.617-1.25.077.077 0 00-.079-.037A19.736 19.736 0 003.677 4.37a.07.07 0 00-.032.027C.533 9.046-.32 13.58.099 18.057a.082.082 0 00.031.057 19.9 19.9 0 005.993 3.03.078.078 0 00.084-.028 14.09 14.09 0 001.226-1.994.076.076 0 00-.041-.106 13.107 13.107 0 01-1.872-.892.077.077 0 01-.008-.128 10.2 10.2 0 00.372-.292.074.074 0 01.077-.01c3.928 1.793 8.18 1.793 12.062 0a.074.074 0 01.078.01c.12.098.246.198.373.292a.077.077 0 01-.006.127 12.299 12.299 0 01-1.873.892.077.077 0 00-.041.107c.36.698.772 1.362 1.225 1.993a.076.076 0 00.084.028 19.839 19.839 0 006.002-3.03.077.077 0 00.032-.054c.5-5.177-.838-9.674-3.549-13.66a.061.061 0 00-.031-.03z" />
            </svg>
            Join Discord
          </a>
        </div>
      </div>

      {/* Central Visual Workflow / Dashboard */}
      <div className="relative w-full max-w-5xl mx-auto mt-16 px-6 lg:px-0 z-20">
        <div className="relative w-full aspect-video rounded-2xl border border-[#00f0ff]/30 bg-[#0c0c0c]/90 backdrop-blur-3xl shadow-[0_0_80px_rgba(0,240,255,0.15)] overflow-hidden flex flex-col">
          {/* Mac window header */}
          <div className="h-10 w-full border-b border-white/10 bg-white/5 flex items-center px-4 gap-2 shrink-0">
            <div className="flex gap-1.5">
              <div className="w-3 h-3 rounded-full bg-[#ff5f56]" />
              <div className="w-3 h-3 rounded-full bg-[#ffbd2e]" />
              <div className="w-3 h-3 rounded-full bg-[#27c93f]" />
            </div>
            <div className="mx-auto text-[10px] uppercase tracking-[0.2em] text-[#888] font-mono">
              Agent Workflow Inspector
            </div>
          </div>

          {/* Workflow Body */}
          <div className="flex-1 relative overflow-hidden flex">
            {/* Sidebar */}
            <div className="w-1/4 border-r border-white/10 bg-white/[0.02] p-4 flex flex-col gap-4 hidden sm:flex">
              <div className="text-xs text-[#00f0ff] uppercase tracking-widest font-bold">
                Active Sessions
              </div>
              <div className="flex flex-col gap-2">
                {[1, 2, 3].map((i) => (
                  <div
                    key={i}
                    className="p-3 rounded-lg border border-white/5 bg-black/20 flex items-center gap-3"
                  >
                    <span
                      className={`w-2 h-2 rounded-full ${i === 1 ? 'bg-[#e8ff47] animate-pulse' : 'bg-white/20'}`}
                    />
                    <div className="flex flex-col">
                      <span className="text-[10px] text-white/80 font-mono">Session_0x{i}F9</span>
                      <span className="text-[9px] text-white/40">Monitoring</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Main Graph Area */}
            <div className="flex-1 relative bg-black/40 p-6 flex items-center justify-center">
              <div className="absolute inset-0 dot-grid opacity-30" />
              <div className="absolute top-4 right-4 flex items-center gap-2 bg-black/60 px-3 py-1.5 rounded-full border border-white/10 backdrop-blur-md">
                <span className="w-2 h-2 rounded-full bg-[#00f0ff] animate-ping" />
                <span className="text-[10px] text-[#00f0ff] font-mono">INTERCEPTING</span>
              </div>

              {/* Simulated Agent Graph Node */}
              <div className="relative z-10 w-full max-w-md">
                <div className="flex justify-between items-center relative">
                  {/* Flow lines */}
                  <div className="absolute left-1/4 right-1/4 h-px bg-gradient-to-r from-[#e8ff47] via-[#00f0ff] to-[#ff6b6b] top-1/2 -translate-y-1/2 z-0" />

                  <div className="relative z-10 w-24 h-24 rounded-full border border-[#e8ff47]/50 bg-[#0c0c0c] flex items-center justify-center shadow-[0_0_30px_rgba(232,255,71,0.2)]">
                    <span className="text-xs text-[#e8ff47] font-mono text-center">
                      LLM
                      <br />
                      Agent
                    </span>
                  </div>

                  <div className="relative z-10 w-32 h-32 rounded-xl border border-[#00f0ff] bg-[#0c0c0c]/90 backdrop-blur-xl flex flex-col items-center justify-center shadow-[0_0_50px_rgba(0,240,255,0.3)] gap-2 p-2">
                    <svg
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                      className="w-8 h-8 text-[#00f0ff]"
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"
                      />
                    </svg>
                    <span className="text-xs text-[#00f0ff] font-mono font-bold">AgentWatch</span>
                  </div>

                  <div className="relative z-10 w-24 h-24 rounded-full border border-[#ff6b6b]/50 bg-[#0c0c0c] flex flex-col items-center justify-center shadow-[0_0_30px_rgba(255,107,107,0.2)] gap-1 p-2 text-center">
                    <span className="text-xs text-[#ff6b6b] font-mono uppercase">
                      Dangerous
                      <br />
                      Action
                    </span>
                    <span className="text-[8px] text-white/50 font-mono">Blocked</span>
                  </div>
                </div>

                {/* Console log simulation inside the video UI */}
                <div className="mt-8 bg-black/80 rounded-lg border border-white/10 p-3 h-24 overflow-hidden relative font-mono text-[10px]">
                  <div className="absolute inset-0 bg-gradient-to-b from-transparent to-black/80 z-10" />
                  <div className="space-y-1 text-white/60 animate-bounce-y">
                    <p>
                      <span className="text-[#e8ff47]">[INF]</span> Agent initiated action:{' '}
                      <code>execute_command</code>
                    </p>
                    <p>
                      <span className="text-[#00f0ff]">[CHK]</span> Analyzing semantic intent...
                    </p>
                    <p>
                      <span className="text-[#00f0ff]">[CHK]</span> Confidence: 0.92, Blast Radius:
                      HIGH
                    </p>
                    <p>
                      <span className="text-[#ff6b6b]">[BLK]</span> Action blocked by rule:
                      SYS_MODIFY
                    </p>
                    <p>
                      <span className="text-[#00f0ff]">[RPT]</span> Fallback triggered. Returning
                      mock result.
                    </p>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div
        ref={scrollRef}
        className="absolute bottom-8 left-1/2 -translate-x-1/2 z-10 flex flex-col items-center gap-1 text-[#888]"
      >
        <span
          className="text-[10px] tracking-[0.25em] uppercase"
          style={{ fontFamily: 'var(--font-jetbrains)' }}
        >
          Scroll
        </span>
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          className="w-5 h-5 animate-bounce-y"
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </div>
    </section>
  );
}
