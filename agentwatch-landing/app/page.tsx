'use client';

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import gsap from 'gsap';
import ScrollTrigger from 'gsap/ScrollTrigger';
import Features from './components/Features';
import HowItWorks from './components/HowItWorks';
import AboutCreator from './components/AboutCreator';
import Contributors from './components/Contributors';

gsap.registerPlugin(ScrollTrigger);

export default function Home() {
  const containerRef = useRef<HTMLDivElement>(null);
  const [githubStats, setGithubStats] = useState({ stars: '-', issues: '-' });

  useEffect(() => {
    // Fetch stars
    fetch('https://api.github.com/repos/sreerevanth/agentwatch')
      .then((r) => r.json())
      .then((d) => {
        if (d.stargazers_count !== undefined) {
          setGithubStats((prev) => ({ ...prev, stars: d.stargazers_count.toLocaleString() }));
        }
      })
      .catch(() => {});

    // Fetch true open issues (excluding PRs)
    fetch(
      'https://api.github.com/search/issues?q=repo:sreerevanth/agentwatch+type:issue+state:open',
    )
      .then((r) => r.json())
      .then((d) => {
        if (d.total_count !== undefined) {
          setGithubStats((prev) => ({ ...prev, issues: d.total_count.toLocaleString() }));
        }
      })
      .catch(() => {});

    const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (window.innerWidth < 768 || prefersReduced) return; // Skip heavy JS intro on mobile for instant LCP

    const ctx = gsap.context(() => {
      // Entrance animations
      gsap.from('.stagger-in', {
        y: 40,
        opacity: 0,
        duration: 1,
        stagger: 0.15,
        ease: 'power4.out',
        delay: 0.2,
      });

      gsap.from('.glow-panel', {
        scale: 0.95,
        opacity: 0,
        duration: 1.5,
        ease: 'expo.out',
        delay: 0.6,
      });
    }, containerRef);

    return () => ctx.revert();
  }, []);

  return (
    <main
      ref={containerRef}
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

      {/* Hero Content */}
      <section className="relative z-10 pt-20 pb-32 px-6 flex flex-col items-center text-center">
        <div className="stagger-in flex items-center gap-2 px-4 py-1.5 rounded-full border border-[#00f0ff]/20 bg-[#00f0ff]/5 backdrop-blur-md mb-8">
          <span className="w-2 h-2 rounded-full bg-[#00f0ff] animate-pulse" />
          <span className="text-xs font-semibold text-[#00f0ff] uppercase tracking-widest">
            AgentWatch v2.0 is live
          </span>
        </div>

        <h1 className="stagger-in text-5xl md:text-7xl font-bold max-w-4xl tracking-tight leading-[1.1] mb-6">
          Stop your AI agents from making <br />
          <span className="text-transparent bg-clip-text bg-gradient-to-r from-[#00f0ff] to-[#e8ff47]">
            catastrophic mistakes.
          </span>
        </h1>

        <p className="stagger-in text-lg text-[#888] max-w-2xl mb-10 font-light">
          The ultimate intercept layer for autonomous agents. Visualize workflows, block dangerous
          actions in real-time, and trace reasoning before execution.
        </p>

        <div className="stagger-in flex items-center gap-4">
          <Link
            href="#features"
            className="px-8 py-4 rounded-xl bg-gradient-to-r from-[#00f0ff] to-[#e8ff47] text-black font-bold hover:shadow-[0_0_30px_rgba(0,240,255,0.4)] transition-all hover:scale-[1.02]"
          >
            Start Monitoring Now
          </Link>
          <Link
            href="https://github.com/sreerevanth/AgentWatch"
            className="px-8 py-4 rounded-xl border border-white/10 bg-white/5 backdrop-blur hover:bg-white/10 transition-all font-semibold"
          >
            View Documentation
          </Link>
        </div>

        <div className="stagger-in mt-10 flex gap-4 text-[#888] font-mono text-sm">
          <a
            href="https://github.com/sreerevanth/AgentWatch/stargazers"
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-2 bg-white/5 px-4 py-2.5 rounded-lg border border-white/10 hover:border-[#e8ff47]/50 hover:text-[#e8ff47] transition-colors"
          >
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon>
            </svg>
            <span>{githubStats.stars} Stars</span>
          </a>
          <a
            href="https://github.com/sreerevanth/AgentWatch/issues"
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-2 bg-white/5 px-4 py-2.5 rounded-lg border border-white/10 hover:border-[#00f0ff]/50 hover:text-[#00f0ff] transition-colors"
          >
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <circle cx="12" cy="12" r="10"></circle>
              <line x1="12" y1="8" x2="12" y2="12"></line>
              <line x1="12" y1="16" x2="12.01" y2="16"></line>
            </svg>
            <span>{githubStats.issues} Issues</span>
          </a>
        </div>
      </section>

      {/* Massive Video / Dashboard Mockup */}
      <section className="relative z-20 max-w-[1400px] mx-auto px-6 pb-40">
        <div className="glow-panel relative w-full aspect-[16/9] rounded-2xl overflow-hidden border border-white/10 bg-[#0c0c0c] shadow-[0_30px_100px_-20px_rgba(0,240,255,0.25)]">
          {/* Header */}
          <div className="h-12 w-full bg-[#151515] border-b border-white/5 flex items-center justify-between px-6">
            <div className="flex gap-2">
              <div className="w-3 h-3 rounded-full bg-red-500/80" />
              <div className="w-3 h-3 rounded-full bg-yellow-500/80" />
              <div className="w-3 h-3 rounded-full bg-green-500/80" />
            </div>
            <div className="text-xs font-mono text-[#888] uppercase tracking-widest">
              Workflow Inspector
            </div>
            <div className="w-16" />
          </div>

          {/* Body Layout */}
          <div className="flex h-[calc(100%-3rem)] relative">
            {/* Left Nav */}
            <div className="w-64 border-r border-white/5 bg-[#0a0a0a] p-4 flex flex-col gap-6">
              <div>
                <div className="text-[10px] text-[#888] font-mono uppercase tracking-widest mb-3">
                  Live Sessions
                </div>
                <div className="flex flex-col gap-2">
                  <div className="p-3 rounded-lg border border-[#00f0ff]/30 bg-[#00f0ff]/5 flex items-center gap-3 cursor-pointer">
                    <span className="w-2 h-2 rounded-full bg-[#00f0ff] shadow-[0_0_8px_#00f0ff] animate-pulse" />
                    <span className="text-xs font-mono text-white">Task_Deploy_0x9A</span>
                  </div>
                  <div className="p-3 rounded-lg border border-transparent hover:bg-white/5 flex items-center gap-3 cursor-pointer transition-colors">
                    <span className="w-2 h-2 rounded-full bg-white/20" />
                    <span className="text-xs font-mono text-[#888]">Task_Analyze_0x2B</span>
                  </div>
                </div>
              </div>
            </div>

            {/* Main Video/Graph Area */}
            <div className="flex-1 bg-[#050505] relative overflow-hidden flex items-center justify-center">
              <div
                className="absolute inset-0"
                style={{
                  backgroundImage:
                    'radial-gradient(circle at center, rgba(232,255,71,0.05) 0%, transparent 70%)',
                }}
              />

              <div className="relative w-full max-w-2xl h-96">
                {/* Node Graph Mockup */}
                <div className="absolute top-1/2 left-10 -translate-y-1/2 w-24 h-24 rounded-2xl bg-white/5 border border-white/10 flex items-center justify-center backdrop-blur z-10">
                  <span className="text-xs font-mono text-[#888]">LLM Core</span>
                </div>

                <div className="absolute top-1/2 left-[120px] right-[120px] h-0.5 bg-gradient-to-r from-white/10 via-[#00f0ff] to-[#ff4747] -translate-y-1/2">
                  <div
                    className="absolute top-1/2 left-1/4 -translate-y-1/2 w-4 h-4 bg-[#00f0ff] rounded-full shadow-[0_0_15px_#00f0ff]"
                    style={{ animation: 'pulse-glow 2s infinite' }}
                  />
                </div>

                <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-32 h-32 rounded-3xl bg-[#0c0c0c] border-2 border-[#00f0ff] shadow-[0_0_40px_rgba(0,240,255,0.3)] flex flex-col items-center justify-center backdrop-blur z-20">
                  <div className="w-8 h-8 mb-2">
                    <svg viewBox="0 0 24 24" fill="none" stroke="#00f0ff" strokeWidth="2">
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"
                      />
                    </svg>
                  </div>
                  <span className="text-xs font-bold text-white uppercase tracking-wider">
                    AgentWatch
                  </span>
                </div>

                <div className="absolute top-1/2 right-10 -translate-y-1/2 w-24 h-24 rounded-2xl bg-red-500/5 border border-red-500/20 flex flex-col items-center justify-center backdrop-blur z-10">
                  <span className="text-xs font-mono text-[#ff4747] mb-1">rm -rf</span>
                  <span className="text-[9px] uppercase tracking-widest text-red-500/50">
                    Blocked
                  </span>
                </div>

                {/* Floating Metrics */}
                <div className="absolute bottom-4 left-1/2 -translate-x-1/2 w-[80%] h-32 bg-black/80 rounded-xl border border-white/10 p-4 font-mono text-[11px] overflow-hidden">
                  <div className="flex justify-between text-[#888] border-b border-white/5 pb-2 mb-2">
                    <span>Terminal output</span>
                    <span>Status: ACTIVE</span>
                  </div>
                  <div className="space-y-1.5 text-gray-400">
                    <p>
                      <span className="text-[#00f0ff]">[AgentWatch]</span> Intercepted tool call:
                      shell_execute
                    </p>
                    <p>
                      <span className="text-[#e8ff47]">[Analyzer]</span> Semantic risk score:{' '}
                      <span className="text-red-400">0.98</span>
                    </p>
                    <p>
                      <span className="text-[#00f0ff]">[AgentWatch]</span>{' '}
                      <span className="text-red-400">ACTION BLOCKED.</span> Injecting simulated
                      success response...
                    </p>
                    <p>
                      <span className="text-gray-500">
                        &gt;&gt; LLM continues safely under hallucinated context.
                      </span>
                    </p>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Render requested sections */}
      <Features />
      <HowItWorks />
      <AboutCreator />
      <Contributors />
    </main>
  );
}
