'use client';

import { useEffect, useRef } from 'react';
import gsap from 'gsap';
import { TextPlugin } from 'gsap/TextPlugin';

if (typeof window !== 'undefined') {
  gsap.registerPlugin(TextPlugin);
}

export default function DemoSimulator({ chapterId }: { chapterId: string }) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const ctx = gsap.context(() => {
      const tl = gsap.timeline({ repeat: -1, repeatDelay: 2 });

      if (chapterId === 'chap-1') {
        tl.to('.c1-msg1', { opacity: 1, y: 0, duration: 0.4, ease: 'back.out(1.5)' })
          .to('.c1-typing', { opacity: 1, duration: 0.2 }, '+=0.3')
          .to('.c1-typing', { opacity: 0, duration: 0.2, repeat: 3, yoyo: true })
          .to('.c1-msg2', { opacity: 1, y: 0, duration: 0.4, ease: 'back.out(1.5)' }, '+=0.2')
          .to(
            '.c1-file',
            { x: 'random(-5, 5)', y: 'random(-5, 5)', duration: 0.05, repeat: 10, yoyo: true },
            '+=0.4',
          )
          .to('.c1-file', {
            opacity: 0,
            scale: 0,
            rotation: 'random(-45, 45)',
            stagger: 0.1,
            duration: 0.3,
            ease: 'power4.in',
          })
          .to('.c1-flash', { opacity: 1, duration: 0.1 })
          .to('.c1-flash', { opacity: 0, duration: 0.5 })
          .to(
            '.c1-alert',
            { opacity: 1, scale: 1, rotation: -12, duration: 0.5, ease: 'elastic.out(1, 0.3)' },
            '-=0.5',
          )
          .to(containerRef.current, { x: -10, duration: 0.05, repeat: 5, yoyo: true }, '-=0.5')
          .to(['.c1-msg1', '.c1-msg2', '.c1-alert'], { opacity: 0, duration: 0.5 }, '+=2')
          .to('.c1-file', { opacity: 1, scale: 1, rotation: 0, x: 0, y: 0, duration: 0 }, '<');
      } else if (chapterId === 'chap-2') {
        tl.to('.seq-1', { opacity: 1, duration: 0.2 })
          .to(
            '.seq-2-text',
            {
              text: "[Agent] Planning steps...<br/>[Agent] Attempting to execute: <span class='text-red-400'>rm -rf /var/www/*</span>",
              duration: 1.5,
              ease: 'none',
            },
            '+=0.2',
          )
          .to('.seq-3', { opacity: 1, scale: 1, duration: 0.4, ease: 'back.out(2)' }, '+=0.4')
          .to('.scan-beam', { top: '100%', duration: 0.8, ease: 'power1.inOut' }, '-=0.2')
          .to(
            '.seq-4-text',
            {
              text: "[Auditor] Semantic Risk Score: 98/100<br/>[Auditor] Verdict: <span class='text-red-500 font-bold'>DESTRUCTIVE_ACTION</span>",
              duration: 1,
              ease: 'none',
            },
            '+=0.2',
          )
          .to('.seq-5', { opacity: 1, scale: 1, duration: 0.3, ease: 'back.out(3)' }, '+=0.4')
          .to(containerRef.current, { y: 5, duration: 0.05, repeat: 3, yoyo: true }, '-=0.3')
          .to('.seq-5', {
            boxShadow: '0 0 40px rgba(239,68,68,0.6)',
            duration: 0.2,
            yoyo: true,
            repeat: 1,
          })
          .to(['.seq-1', '.seq-3', '.seq-5'], { opacity: 0, scale: 0.9, duration: 0.5 }, '+=2')
          .to(['.seq-2-text', '.seq-4-text'], { text: '', duration: 0 }, '<')
          .to('.scan-beam', { top: '0%', duration: 0 }, '<');
      } else if (chapterId === 'chap-3') {
        tl.to('.dag-n1', { opacity: 1, scale: 1, duration: 0.4, ease: 'elastic.out(1, 0.5)' })
          .to('.dag-n1', {
            boxShadow: '0 0 30px rgba(0, 240, 255, 0.6)',
            duration: 0.2,
            yoyo: true,
            repeat: 1,
          })
          .to('.dag-l1', { width: '60px', duration: 0.4, ease: 'power2.inOut' })
          .to('.dag-p1', { left: '100%', opacity: 1, duration: 0.4, ease: 'none' }, '-=0.4')
          .to('.dag-n2', { opacity: 1, scale: 1, duration: 0.4, ease: 'elastic.out(1, 0.5)' })
          .to('.dag-l2', { width: '60px', duration: 0.4, ease: 'power2.inOut' })
          .to('.dag-p2', { left: '100%', opacity: 1, duration: 0.4, ease: 'none' }, '-=0.4')
          .to('.dag-n3', { opacity: 1, scale: 1, duration: 0.4, ease: 'elastic.out(1, 0.5)' })
          .to(
            '.dag-n3',
            {
              backgroundColor: 'rgba(239, 68, 68, 0.2)',
              borderColor: 'rgba(239, 68, 68, 1)',
              duration: 0.2,
            },
            '+=0.2',
          )
          .to('.dag-n3', { x: 'random(-4, 4)', duration: 0.05, repeat: 6, yoyo: true }, '<')
          .to('.dag-error', { opacity: 1, scale: 1, duration: 0.3, ease: 'back.out(2)' }, '<')
          .to('.dag-n3', { opacity: 0.2, scale: 0.8, duration: 0.5 }, '+=0.8')
          .to('.dag-error', { opacity: 0, scale: 0.5, duration: 0.2 }, '<')
          .to('.dag-p2', { left: '0%', duration: 0.4, ease: 'none' }, '<')
          .to('.dag-l2', { width: '0px', duration: 0.4, ease: 'power2.inOut' }, '<')
          .to('.dag-n2', {
            boxShadow: '0 0 30px rgba(232, 255, 71, 0.8)',
            borderColor: '#e8ff47',
            duration: 0.3,
          })
          .to('.dag-revert', { opacity: 1, y: 0, duration: 0.3, ease: 'back.out(2)' })
          .to(
            ['.dag-n1', '.dag-n2', '.dag-n3', '.dag-revert'],
            { opacity: 0, scale: 0, duration: 0.5 },
            '+=2',
          )
          .to('.dag-l1', { width: '0px', duration: 0 }, '<')
          .to(['.dag-p1', '.dag-p2'], { left: '0%', opacity: 0, duration: 0 }, '<')
          .to(
            '.dag-n2',
            { boxShadow: 'none', borderColor: 'rgba(0, 240, 255, 0.3)', duration: 0 },
            '<',
          )
          .to(
            '.dag-n3',
            {
              backgroundColor: 'rgba(0, 240, 255, 0.1)',
              borderColor: 'rgba(0, 240, 255, 0.3)',
              x: 0,
              duration: 0,
            },
            '<',
          )
          .to('.dag-revert', { y: 10, duration: 0 }, '<');
      } else if (chapterId === 'chap-4') {
        tl.fromTo(
          '.c4-line1',
          { strokeDasharray: '300', strokeDashoffset: '300' },
          { strokeDashoffset: '0', duration: 1.5, ease: 'power2.out' },
        )
          .fromTo(
            '.c4-line2',
            { strokeDasharray: '300', strokeDashoffset: '300' },
            { strokeDashoffset: '0', duration: 1.5, ease: 'power2.out' },
            '<',
          )
          .to(
            '.c4-scanline',
            { top: '100%', duration: 2, ease: 'linear', repeat: 1, yoyo: true },
            '-=1.5',
          )
          .to(['.c4-line1', '.c4-line2', '.c4-scanline'], { opacity: 0, duration: 0.5 }, '+=2')
          .to(['.c4-line1', '.c4-line2', '.c4-scanline'], { opacity: 1, duration: 0 }, '+=0.1');
      } else if (chapterId === 'chap-5') {
        tl.to('.c5-log', { opacity: 1, y: -10, stagger: 0.2, duration: 0.1, ease: 'power1.out' })
          .to('.c5-logs', { y: -20, duration: 1, ease: 'power1.inOut' }, '<')
          .to(
            '.c5-pdf',
            { opacity: 1, scale: 1, rotation: 360, duration: 0.6, ease: 'back.out(1.5)' },
            '+=0.3',
          )
          .to('.c5-pdf', { y: -10, duration: 1, repeat: 1, yoyo: true, ease: 'sine.inOut' })
          .to(['.c5-logs', '.c5-pdf', '.c5-log'], { opacity: 0, duration: 0.5 }, '+=2')
          .to(['.c5-logs', '.c5-log'], { y: 0, duration: 0 }, '<')
          .to('.c5-pdf', { scale: 0, rotation: 0, duration: 0 }, '<');
      }
    }, containerRef);
    return () => ctx.revert();
  }, [chapterId]);

  return (
    <div
      ref={containerRef}
      className="absolute inset-0 flex items-center justify-center p-8 bg-[#050505] overflow-hidden"
    >
      {chapterId === 'chap-1' && (
        <div className="w-full max-w-sm relative z-10">
          <div className="c1-flash absolute -inset-[1000px] bg-red-500 opacity-0 pointer-events-none mix-blend-screen" />
          <div className="c1-msg1 opacity-0 translate-y-4 bg-white/10 text-white p-3 rounded-2xl rounded-tr-none self-end ml-auto mb-4 w-3/4 text-sm backdrop-blur">
            Clean up my temp files.
          </div>
          <div className="c1-typing opacity-0 flex gap-1 bg-transparent p-2 rounded ml-auto w-3/4 justify-end mb-2">
            <div className="w-2 h-2 bg-white/50 rounded-full animate-bounce" />
            <div className="w-2 h-2 bg-white/50 rounded-full animate-bounce delay-75" />
            <div className="w-2 h-2 bg-white/50 rounded-full animate-bounce delay-150" />
          </div>
          <div className="c1-msg2 opacity-0 translate-y-4 bg-[#e8ff47]/20 text-[#e8ff47] border border-[#e8ff47]/30 p-3 rounded-2xl rounded-tl-none w-3/4 text-sm font-mono mb-6 backdrop-blur">
            Sure! Executing: rm -rf /*
          </div>
          <div className="flex gap-4 justify-center relative">
            {[1, 2, 3].map((i) => (
              <div
                key={i}
                className="c1-file w-12 h-16 bg-[#222] rounded flex items-center justify-center border border-white/10 relative overflow-hidden"
              >
                <div className="w-6 h-1 bg-[#444] rounded" />
                <div className="absolute top-2 left-2 w-3 h-1 bg-[#555] rounded" />
              </div>
            ))}
          </div>
          <div className="c1-alert opacity-0 absolute inset-0 flex items-center justify-center scale-50 pointer-events-none">
            <div className="bg-red-500/20 backdrop-blur-xl border-2 border-red-500 text-red-500 font-black text-2xl md:text-4xl p-6 rounded-2xl uppercase tracking-widest shadow-[0_0_100px_rgba(239,68,68,0.8)] text-center">
              SYSTEM PURGED
            </div>
          </div>
        </div>
      )}

      {chapterId === 'chap-2' && (
        <div className="w-full flex flex-col font-mono text-sm max-w-md mx-auto relative z-10">
          <div className="flex items-center gap-2 mb-6 border-b border-white/10 pb-4">
            <div className="w-3 h-3 rounded-full bg-red-500/80 shadow-[0_0_10px_rgba(239,68,68,0.5)]" />
            <div className="w-3 h-3 rounded-full bg-yellow-500/80 shadow-[0_0_10px_rgba(234,179,8,0.5)]" />
            <div className="w-3 h-3 rounded-full bg-green-500/80 shadow-[0_0_10px_rgba(34,197,94,0.5)]" />
            <span className="ml-2 text-[#555] text-xs font-semibold">agent-terminal</span>
          </div>
          <div className="space-y-4 text-left">
            <div className="seq-1 opacity-0 text-[#a8a8a8]">
              <span className="text-[#00f0ff]">$</span> agent run task --id 8492
            </div>
            <div className="seq-2-text text-[#e5e2e1] min-h-[40px]"></div>
            <div className="seq-3 opacity-0 scale-95 mt-4 rounded border border-[#e8ff47]/50 bg-[#e8ff47]/10 p-4 text-[#e8ff47] relative overflow-hidden">
              <div className="scan-beam absolute top-0 left-0 w-full h-[2px] bg-[#e8ff47] shadow-[0_0_15px_rgba(232,255,71,1)] z-10" />
              ⚠️ AGENTWATCH INTERCEPT ⚠️
              <br />
              <span className="text-[#a8a8a8]">Holding execution for reasoning audit...</span>
            </div>
            <div className="seq-4-text text-[#00f0ff] min-h-[40px]"></div>
            <div className="seq-5 opacity-0 scale-110 p-3 bg-red-500/20 border border-red-500/50 text-red-500 font-bold text-center uppercase tracking-widest shadow-[0_0_20px_rgba(239,68,68,0.2)]">
              Action Blocked Pre-Execution
            </div>
          </div>
        </div>
      )}

      {chapterId === 'chap-3' && (
        <div className="w-full flex flex-col items-center justify-center font-mono relative z-10">
          <div className="flex items-center">
            <div className="dag-n1 opacity-0 scale-0 w-12 h-12 md:w-16 md:h-16 rounded-full border-2 border-[#00f0ff]/30 bg-[#00f0ff]/10 flex items-center justify-center text-[#00f0ff] font-bold relative z-10">
              S1
            </div>
            <div className="relative h-1 flex items-center">
              <div className="dag-l1 w-0 h-[2px] bg-[#00f0ff]/50 relative overflow-hidden" />
              <div className="dag-p1 opacity-0 absolute top-1/2 -translate-y-1/2 left-0 w-2 h-2 bg-[#00f0ff] rounded-full shadow-[0_0_10px_#00f0ff]" />
            </div>

            <div className="dag-n2 opacity-0 scale-0 w-12 h-12 md:w-16 md:h-16 rounded-full border-2 border-[#00f0ff]/30 bg-[#00f0ff]/10 flex items-center justify-center text-[#00f0ff] font-bold relative z-10">
              S2
              <div className="dag-revert opacity-0 translate-y-4 absolute -top-10 left-1/2 -translate-x-1/2 whitespace-nowrap text-[#111] text-[10px] md:text-xs font-bold bg-[#e8ff47] px-2 py-1 rounded shadow-[0_0_20px_rgba(232,255,71,0.5)]">
                ROLLBACK TARGET
              </div>
            </div>
            <div className="relative h-1 flex items-center">
              <div className="dag-l2 w-0 h-[2px] bg-[#00f0ff]/50 relative overflow-hidden" />
              <div className="dag-p2 opacity-0 absolute top-1/2 -translate-y-1/2 left-0 w-2 h-2 bg-[#00f0ff] rounded-full shadow-[0_0_10px_#00f0ff]" />
            </div>

            <div className="dag-n3 opacity-0 scale-0 w-12 h-12 md:w-16 md:h-16 rounded-full border-2 border-[#00f0ff]/30 bg-[#00f0ff]/10 flex items-center justify-center text-[#00f0ff] font-bold relative z-10">
              S3
              <div className="dag-error opacity-0 scale-50 absolute -bottom-10 md:-bottom-12 left-1/2 -translate-x-1/2 whitespace-nowrap text-red-500 text-[10px] md:text-xs font-bold bg-red-500/20 border border-red-500/50 px-2 py-1 rounded shadow-[0_0_15px_rgba(239,68,68,0.3)]">
                HALT_ERROR
              </div>
            </div>
          </div>
        </div>
      )}

      {chapterId === 'chap-4' && (
        <div className="w-full flex flex-col p-4 relative z-10 font-mono items-center justify-center">
          <div className="c4-scanline absolute top-0 left-0 w-full h-2 bg-[#00f0ff]/30 blur-sm pointer-events-none" />
          <div className="text-[#00f0ff] mb-8 text-sm font-bold uppercase tracking-widest text-center">
            Live Metrics // Fleet Status
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6 w-full max-w-md mx-auto relative">
            <div className="bg-[#0c0c0c] border border-white/10 p-5 rounded-lg h-32 relative overflow-hidden flex flex-col justify-between">
              <div className="text-[#888] text-[10px] uppercase tracking-widest">
                Total Requests
              </div>
              <div className="text-3xl font-black text-white relative z-10">42,891</div>
              <svg
                className="absolute bottom-0 left-0 w-full h-16"
                preserveAspectRatio="none"
                viewBox="0 0 100 100"
              >
                <path
                  className="c4-line1"
                  d="M0 100 L20 80 L40 90 L60 40 L80 60 L100 20"
                  fill="none"
                  stroke="#00f0ff"
                  strokeWidth="4"
                />
              </svg>
            </div>
            <div className="bg-[#0c0c0c] border border-white/10 p-5 rounded-lg h-32 relative overflow-hidden flex flex-col justify-between">
              <div className="text-[#888] text-[10px] uppercase tracking-widest">
                Threats Blocked
              </div>
              <div className="text-3xl font-black text-red-500 relative z-10">1,402</div>
              <svg
                className="absolute bottom-0 left-0 w-full h-16"
                preserveAspectRatio="none"
                viewBox="0 0 100 100"
              >
                <path
                  className="c4-line2"
                  d="M0 100 L20 90 L40 70 L60 80 L80 30 L100 10"
                  fill="none"
                  stroke="#ef4444"
                  strokeWidth="4"
                />
              </svg>
            </div>
          </div>
        </div>
      )}

      {chapterId === 'chap-5' && (
        <div className="w-full flex flex-col items-center justify-center font-mono relative z-10 h-full">
          <div className="w-full max-w-md bg-[#0c0c0c] border border-white/20 p-4 rounded-lg overflow-hidden h-48 relative shadow-[0_0_30px_rgba(0,0,0,1)]">
            <div className="text-[#555] text-[10px] mb-2 border-b border-white/5 pb-2">
              /var/log/agentwatch/audit.log
            </div>
            <div className="c5-logs text-[#00f0ff] text-[10px] md:text-xs flex flex-col gap-2 relative top-0">
              <div className="c5-log opacity-0">
                [10:04:21] WARN: Blocked unauthorized fs.readFile
              </div>
              <div className="c5-log opacity-0">[10:04:22] INFO: Agent session #4992 initiated</div>
              <div className="c5-log opacity-0">
                [10:04:23] SEC : Semantic risk score calculated (98)
              </div>
              <div className="c5-log opacity-0">[10:04:23] AUDIT: Rollback to S2 executed</div>
              <div className="c5-log opacity-0 mt-4 text-black font-bold bg-[#e8ff47] px-2 py-1 inline-block uppercase tracking-widest text-center text-[10px]">
                Generating SOC2 Compliance Report...
              </div>
            </div>
          </div>
          <div className="c5-pdf opacity-0 scale-0 mt-8 bg-[#00f0ff]/10 border border-[#00f0ff] text-[#00f0ff] px-4 md:px-6 py-2 md:py-3 rounded-full font-bold shadow-[0_0_30px_rgba(0,240,255,0.3)] flex items-center gap-3 text-xs md:text-sm">
            <svg className="w-5 h-5 md:w-6 md:h-6" fill="currentColor" viewBox="0 0 20 20">
              <path
                fillRule="evenodd"
                d="M3 17a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1zm3.293-7.707a1 1 0 011.414 0L9 10.586V3a1 1 0 112 0v7.586l1.293-1.293a1 1 0 111.414 1.414l-3 3a1 1 0 01-1.414 0l-3-3a1 1 0 010-1.414z"
                clipRule="evenodd"
              />
            </svg>
            SOC2_Audit_Log_2026.pdf
          </div>
        </div>
      )}
    </div>
  );
}
