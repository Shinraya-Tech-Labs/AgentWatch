'use client';

import { useEffect, useRef } from 'react';
import gsap from 'gsap';
import ScrollTrigger from 'gsap/ScrollTrigger';

gsap.registerPlugin(ScrollTrigger);

const features = [
  {
    title: 'Reasoning Auditor',
    desc: 'Independent model scores every step before it executes.',
    color: '#00f0ff',
  },
  {
    title: 'Safety Engine',
    desc: 'Blocks 40+ dangerous patterns pre-execution, not post-hoc.',
    color: '#e8ff47',
  },
  {
    title: 'One-Click Rollback',
    desc: 'Git-backed checkpoints at every step. Irreversible becomes reversible.',
    color: '#ff4747',
  },
  {
    title: 'Multi-Agent DAG',
    desc: 'Trace failures across agent boundaries. Find the root cause.',
    color: '#00f0ff',
  },
  {
    title: 'Causal Memory',
    desc: 'Cross-session reasoning trails. Why did we choose X last week?',
    color: '#e8ff47',
  },
  {
    title: 'Compliance Ready',
    desc: 'GDPR, HIPAA, EU AI Act. One-click audit exports.',
    color: '#ff4747',
  },
];

export default function Features() {
  const containerRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const ctx = gsap.context(() => {
      gsap.fromTo(
        '.feature-card',
        { y: 40, opacity: 0 },
        {
          y: 0,
          opacity: 1,
          duration: 0.8,
          stagger: 0.1,
          ease: 'power3.out',
          scrollTrigger: {
            trigger: containerRef.current,
            start: 'top 75%',
            once: true,
          },
        },
      );
    }, containerRef);
    return () => ctx.revert();
  }, []);

  return (
    <section
      id="features"
      ref={containerRef}
      className="relative z-10 py-32 px-6 max-w-7xl mx-auto"
    >
      <div className="flex flex-col items-center text-center mb-16">
        <h2 className="text-4xl md:text-5xl font-bold tracking-tight mb-4 text-transparent bg-clip-text bg-gradient-to-r from-white to-gray-500">
          Everything you need.
        </h2>
        <p className="text-[#888] font-mono text-xs uppercase tracking-[0.2em]">
          6 modules · production-grade
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {features.map((f, i) => (
          <div key={i} className="feature-card relative group">
            <div className="absolute inset-0 bg-gradient-to-br from-white/5 to-transparent opacity-0 group-hover:opacity-100 transition-opacity rounded-2xl" />
            <div className="h-full p-8 rounded-2xl border border-white/5 bg-[#0a0a0a] hover:border-white/20 transition-colors">
              <div
                className="w-10 h-10 rounded-lg flex items-center justify-center mb-6"
                style={{ backgroundColor: `${f.color}20` }}
              >
                <div
                  className="w-2 h-2 rounded-full"
                  style={{ backgroundColor: f.color, boxShadow: `0 0 10px ${f.color}` }}
                />
              </div>
              <h3 className="text-xl font-bold mb-3 text-white">{f.title}</h3>
              <p className="text-[#888] text-sm leading-relaxed">{f.desc}</p>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
