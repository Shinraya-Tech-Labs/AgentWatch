'use client';

import { useEffect, useRef } from 'react';
import gsap from 'gsap';
import ScrollTrigger from 'gsap/ScrollTrigger';

gsap.registerPlugin(ScrollTrigger);

interface Metric {
  /** Final numeric value to count to */
  to: number;
  /** Number formatter (called with current value during count-up) */
  format: (v: number) => string;
  label: string;
}

const METRICS: Metric[] = [
  {
    to: 1200000,
    format: (v) => `${(v / 1_000_000).toFixed(1)}M+`,
    label: 'spans analyzed',
  },
  {
    to: 8,
    format: (v) => `${Math.round(v)}`,
    label: 'framework adapters',
  },
  {
    to: 205,
    format: (v) => `${Math.round(v)}`,
    label: 'tests passing',
  },
  {
    to: 40,
    format: (v) => `${Math.round(v)}+`,
    label: 'dangerous patterns',
  },
  {
    to: 100,
    format: (v) => `${Math.round(v)}%`,
    label: 'open source',
  },
];

export default function TrustMetrics() {
  const sectionRef = useRef<HTMLElement>(null);
  const valueRefs = useRef<(HTMLSpanElement | null)[]>([]);

  useEffect(() => {
    const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    const ctx = gsap.context(() => {
      gsap.from('.trust-item', {
        y: 20,
        opacity: 0,
        duration: 0.7,
        stagger: 0.1,
        ease: 'power3.out',
        scrollTrigger: { trigger: sectionRef.current, start: 'top 90%', once: true },
      });

      // Count-up animations
      METRICS.forEach((m, i) => {
        const el = valueRefs.current[i];
        if (!el) return;

        if (prefersReduced) {
          el.textContent = m.format(m.to);
          return;
        }

        const counter = { val: 0 };
        ScrollTrigger.create({
          trigger: sectionRef.current,
          start: 'top 90%',
          once: true,
          onEnter: () => {
            gsap.to(counter, {
              val: m.to,
              duration: 1.8,
              ease: 'power2.out',
              onUpdate: () => {
                el.textContent = m.format(counter.val);
              },
            });
          },
        });
      });
    }, sectionRef);

    setTimeout(() => ScrollTrigger.refresh(), 100);
    return () => ctx.revert();
  }, []);

  return (
    <section
      ref={sectionRef}
      className="relative px-6"
      aria-label="Trust metrics"
      style={{
        background: '#0d0d0d',
        borderTop: '1px solid rgba(232,255,71,0.2)',
        borderBottom: '1px solid rgba(232,255,71,0.2)',
        boxShadow: '0 0 30px rgba(232,255,71,0.04), inset 0 0 60px rgba(232,255,71,0.015)',
      }}
    >
      {/* Subtle scan sweep across the strip */}
      <div
        aria-hidden="true"
        className="absolute inset-y-0 pointer-events-none"
        style={{
          width: '15%',
          background: 'linear-gradient(90deg, transparent, rgba(232,255,71,0.06), transparent)',
          animation: 'scan-sweep 8s linear infinite',
        }}
      />

      <div className="max-w-6xl mx-auto py-9 flex flex-wrap items-stretch justify-around gap-y-6">
        {METRICS.map((m, i) => (
          <div
            key={i}
            className={
              'trust-item flex flex-col items-center text-center px-6 ' +
              (i < METRICS.length - 1 ? 'md:border-r md:border-white/8' : '')
            }
            style={{ minWidth: '140px' }}
          >
            <span
              ref={(el) => {
                valueRefs.current[i] = el;
              }}
              className="text-[#e8ff47] text-2xl font-bold tracking-tight"
              style={{ fontFamily: 'var(--font-jetbrains)' }}
            >
              0
            </span>
            <span
              className="text-[11px] uppercase tracking-[0.18em] text-[#888] mt-1.5"
              style={{ fontFamily: 'var(--font-jetbrains)' }}
            >
              {m.label}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}
