'use client';

import { useEffect, useRef } from 'react';
import gsap from 'gsap';
import ScrollTrigger from 'gsap/ScrollTrigger';

gsap.registerPlugin(ScrollTrigger);

const CODE_LINES = [
  { text: '# Install', type: 'comment' },
  { text: 'pip install agentwatch-ai', type: 'cmd' },
  { text: '', type: 'empty' },
  { text: '# Watch any agent', type: 'comment' },
  { text: 'agentwatch watch "python my_agent.py"', type: 'cmd' },
  { text: '', type: 'empty' },
  { text: '# Or wrap in Python', type: 'comment' },
  { text: 'from agentwatch import watch', type: 'code' },
  { text: 'agent = watch(your_agent)', type: 'code' },
];

const frameworks = ['LangChain', 'CrewAI', 'AutoGPT', 'Claude Code', 'LangGraph', 'AutoGen'];

function lineColor(type: string) {
  if (type === 'comment') return '#555';
  if (type === 'cmd') return '#e8ff47';
  return '#e5e2e1';
}

function renderStaticLines(container: HTMLDivElement) {
  container.innerHTML = '';
  CODE_LINES.forEach((line) => {
    const el = document.createElement('div');
    el.className = 'leading-7';
    if (line.type === 'empty') {
      el.innerHTML = '&nbsp;';
    } else {
      el.style.color = lineColor(line.type);
      el.textContent = line.text;
    }
    container.appendChild(el);
  });
}

function typeLines(container: HTMLDivElement) {
  container.innerHTML = '';
  let delay = 0;
  CODE_LINES.forEach((line) => {
    const el = document.createElement('div');
    el.className = 'leading-7';
    if (line.type === 'empty') {
      el.innerHTML = '&nbsp;';
      setTimeout(() => container.appendChild(el), delay);
      delay += 50;
      return;
    }
    el.style.color = lineColor(line.type);
    setTimeout(() => {
      container.appendChild(el);
      let i = 0;
      const chars = line.text.split('');
      const tick = () => {
        if (i < chars.length) {
          el.textContent = (el.textContent ?? '') + chars[i++];
          setTimeout(tick, 22);
        }
      };
      tick();
    }, delay);
    delay += line.text.length * 22 + 110;
  });
}

export default function QuickStart() {
  const sectionRef = useRef<HTMLElement>(null);
  const leftRef = useRef<HTMLDivElement>(null);
  const terminalRef = useRef<HTMLDivElement>(null);
  const linesRef = useRef<HTMLDivElement>(null);
  const triggered = useRef(false);

  useEffect(() => {
    const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const container = linesRef.current;
    if (!container) return;

    if (prefersReduced) {
      renderStaticLines(container);
      return;
    }

    const ctx = gsap.context(() => {
      gsap.fromTo(
        leftRef.current,
        { x: -40, opacity: 0 },
        {
          x: 0,
          opacity: 1,
          duration: 0.8,
          ease: 'power3.out',
          scrollTrigger: { trigger: leftRef.current, start: 'top 80%', once: true },
        },
      );

      gsap.fromTo(
        terminalRef.current,
        { x: 40, opacity: 0 },
        {
          x: 0,
          opacity: 1,
          duration: 0.8,
          ease: 'power3.out',
          scrollTrigger: { trigger: terminalRef.current, start: 'top 80%', once: true },
        },
      );

      ScrollTrigger.create({
        trigger: terminalRef.current,
        start: 'top 80%',
        once: true,
        onEnter: () => {
          if (triggered.current) return;
          triggered.current = true;
          typeLines(container);
        },
      });
    }, sectionRef);

    setTimeout(() => ScrollTrigger.refresh(), 100);
    return () => ctx.revert();
  }, []);

  return (
    <section
      ref={sectionRef}
      className="py-16 px-6"
      style={{
        background:
          'radial-gradient(ellipse 65% 70% at 50% 50%, rgba(10,10,10,0.85) 0%, rgba(10,10,10,0.45) 55%, transparent 92%)',
      }}
    >
      <div className="max-w-6xl mx-auto grid grid-cols-1 lg:grid-cols-2 gap-16 items-center">
        {/* Left */}
        <div ref={leftRef} className="flex flex-col gap-6">
          <h2
            className="font-bold"
            style={{
              fontFamily: 'var(--font-syne)',
              fontSize: 'clamp(1.8rem, 3.5vw, 2.8rem)',
            }}
          >
            Start watching in <span className="gradient-text">60 seconds.</span>
          </h2>
          <p className="text-[#b8b8b8] text-lg leading-relaxed">
            Wraps any agent framework.
            <br />
            Zero config. Real data immediately.
          </p>

          {/* Framework badges */}
          <div className="flex flex-wrap gap-2">
            {frameworks.map((fw) => (
              <span
                key={fw}
                className="px-3 py-1.5 rounded-full text-xs border border-white/10 bg-[#0d0d0d]/70 backdrop-blur-sm text-[#b8b8b8] hover:border-[#e8ff47]/40 hover:text-[#e5e2e1] transition-all duration-200"
              >
                {fw}
              </span>
            ))}
          </div>
        </div>

        {/* Right: Terminal */}
        <div
          ref={terminalRef}
          className="rounded-xl overflow-hidden border border-white/10 shadow-2xl"
          style={{
            backdropFilter: 'blur(16px)',
            WebkitBackdropFilter: 'blur(16px)',
          }}
        >
          {/* macOS chrome */}
          <div className="flex items-center gap-2 px-4 py-3 bg-[#1a1a1a]/95 border-b border-white/5">
            <span className="w-3 h-3 rounded-full bg-[#ff5f57]" />
            <span className="w-3 h-3 rounded-full bg-[#ffbd2e]" />
            <span className="w-3 h-3 rounded-full bg-[#28c840]" />
            <span
              className="ml-4 text-xs text-[#555]"
              style={{ fontFamily: 'var(--font-jetbrains)' }}
            >
              terminal
            </span>
          </div>

          {/* Code area */}
          <div
            className="bg-[#0d0d0d]/95 px-6 py-5 min-h-[280px]"
            style={{ fontFamily: 'var(--font-jetbrains)', fontSize: '0.82rem' }}
          >
            {/* linesRef starts empty; filled by useEffect */}
            <div ref={linesRef} />
            <span className="inline-block w-2 h-4 bg-[#e8ff47] animate-blink mt-1" />
          </div>
        </div>
      </div>
    </section>
  );
}
