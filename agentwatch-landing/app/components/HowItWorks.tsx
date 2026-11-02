'use client';

import { useState, useEffect } from 'react';
import { motion, AnimatePresence, Reorder } from 'framer-motion';
import DemoSimulator from './DemoSimulator';

const CHAPTERS = [
  {
    id: 'chap-1',
    title: 'Chapter 1: The Rogue AI',
    subtitle: 'For Everyone',
    time: '2:15',
    desc: 'Without AgentWatch, AI agents have unchecked access. A simple misunderstanding can lead to catastrophic data loss or broken systems before you even realize what happened.',
    points: ['Unchecked access risk', 'Catastrophic data loss', 'System corruption'],
    status: 'READY',
    videoSrc: 'https://storage.googleapis.com/gtv-videos-bucket/sample/ElephantsDream.mp4',
  },
  {
    id: 'chap-2',
    title: 'Chapter 2: The Interceptor',
    subtitle: 'How It Works',
    time: '1:45',
    desc: "AgentWatch acts as a pre-execution firewall. It intercepts the AI's intended actions, runs a rapid semantic risk analysis, and blocks destructive commands before they execute.",
    points: ['Pre-execution firewall', 'Semantic risk analysis', 'Real-time blocking'],
    status: 'READY',
    videoSrc: 'https://storage.googleapis.com/gtv-videos-bucket/sample/TearsOfSteel.mp4',
  },
  {
    id: 'chap-3',
    title: 'Chapter 3: DAG Tracing & Rollback',
    subtitle: 'For High-Level Devs',
    time: '3:10',
    desc: 'Every action is traced in a Directed Acyclic Graph. If an agent hallucinates deep in a workflow, AgentWatch automatically reverts the system state to the last safe node and corrects the prompt.',
    points: ['DAG state tracing', 'Automated rollbacks', 'Prompt correction'],
    status: 'READY',
    videoSrc: 'https://storage.googleapis.com/gtv-videos-bucket/sample/Sintel.mp4',
  },
  {
    id: 'chap-4',
    title: 'Chapter 4: Real-time Observability',
    subtitle: 'For the Control Center',
    time: '2:30',
    desc: 'Monitor your fleet of AI agents in real-time. AgentWatch provides a unified dashboard that visualizes agent metrics, decision pathways, and risk flags across your entire infrastructure.',
    points: ['Unified fleet dashboard', 'Decision pathway tracing', 'Risk flagging'],
    status: 'READY',
    videoSrc: 'https://storage.googleapis.com/gtv-videos-bucket/sample/ForBiggerBlazes.mp4',
  },
  {
    id: 'chap-5',
    title: 'Chapter 5: Immutable Audit Logs',
    subtitle: 'For Enterprise & Security',
    time: '1:55',
    desc: 'Every action, decision, and intercepted threat is immutably logged. Export SOC2-ready audit reports instantly, ensuring complete transparency and compliance for AI behavior.',
    points: ['Immutable logging', 'SOC2-ready reports', 'Complete transparency'],
    status: 'READY',
    videoSrc: 'https://storage.googleapis.com/gtv-videos-bucket/sample/ForBiggerJoyrides.mp4',
  },
];

export default function HowItWorks() {
  const [chapters, setChapters] = useState(CHAPTERS);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  // Close on ESC
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setExpandedId(null);
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  const expandedChapter = chapters.find((c) => c.id === expandedId);

  return (
    <section
      id="how-it-works"
      className="relative w-full min-h-screen bg-[#050505] flex items-center overflow-hidden py-32 px-6 border-t border-white/5"
    >
      {/* Background Effects */}
      <div className="absolute inset-0 pointer-events-none z-0">
        <div className="absolute inset-0 bg-[linear-gradient(rgba(0,240,255,0.03)_1px,transparent_1px),linear-gradient(90deg,rgba(0,240,255,0.03)_1px,transparent_1px)] bg-[size:64px_64px] [mask-image:radial-gradient(ellipse_60%_60%_at_50%_50%,#000_20%,transparent_100%)]" />
      </div>

      <div className="max-w-7xl mx-auto w-full flex flex-col lg:flex-row gap-12 relative z-10 items-center lg:items-start">
        {/* Left Side: Holder */}
        <div className="w-full lg:w-[450px] flex-shrink-0 relative">
          <div className="mb-10 text-center lg:text-left">
            <div className="inline-block px-4 py-1.5 rounded-full border border-[#00f0ff]/30 bg-[#00f0ff]/10 text-[#00f0ff] text-xs font-mono font-bold uppercase tracking-[0.3em] mb-4">
              SYSTEM_DEMONSTRATION
            </div>
            <h2
              className="text-4xl md:text-5xl font-bold tracking-tighter mb-4 text-white"
              style={{ fontFamily: 'var(--font-syne)' }}
            >
              Intelligent Archive
            </h2>
            <p className="text-[#888] font-light text-lg">
              Drag a classified file out of the holder to inspect the system demonstration.
            </p>
          </div>

          {/* The Physical Holder */}
          <div className="relative p-6 pt-10 rounded-3xl bg-gradient-to-b from-[#0a0a0a]/90 to-[#050505]/90 backdrop-blur-xl border border-white/10 shadow-[inset_0_2px_10px_rgba(255,255,255,0.05),0_20px_50px_rgba(0,0,0,0.8)] overflow-hidden">
            {/* Neon Accent Edge */}
            <div className="absolute top-0 left-0 right-0 h-[1px] bg-gradient-to-r from-transparent via-[#00f0ff]/50 to-transparent" />

            <Reorder.Group
              axis="y"
              values={chapters}
              onReorder={setChapters}
              className="flex flex-col relative z-10"
              style={{ gap: '-20px' }}
            >
              {chapters.map((chap, index) => {
                const isExpanded = expandedId === chap.id;

                return (
                  <Reorder.Item
                    key={chap.id}
                    value={chap}
                    className="relative cursor-grab active:cursor-grabbing mb-[-1.5rem] last:mb-0"
                    onDragEnd={(e, info) => {
                      // Trigger open if dragged sufficiently far
                      if (Math.abs(info.offset.x) > 100 || Math.abs(info.offset.y) > 100) {
                        setExpandedId(chap.id);
                      }
                    }}
                    onClick={() => {
                      // Also allow clicking for mobile/accessibility
                      if (window.innerWidth < 768) setExpandedId(chap.id);
                    }}
                  >
                    <motion.div
                      layoutId={`card-container-${chap.id}`}
                      className={`p-5 pt-6 rounded-2xl border border-white/10 bg-[#0c0c0c] shadow-[0_-5px_15px_rgba(0,0,0,0.5)] transition-all hover:border-[#00f0ff]/40 hover:bg-[#111] group`}
                      whileHover={{ y: -5, scale: 1.01 }}
                      whileTap={{ scale: 0.98 }}
                      style={{ opacity: isExpanded ? 0 : 1 }}
                    >
                      <div className="flex justify-between items-start mb-2">
                        <span className="text-[10px] font-mono text-[#00f0ff] uppercase tracking-widest">
                          {chap.subtitle}
                        </span>
                        <span className="text-[10px] font-mono text-[#555] group-hover:text-[#888] transition-colors">
                          {chap.time}
                        </span>
                      </div>
                      <h3 className="text-lg font-bold text-[#e5e5e5] group-hover:text-white transition-colors mb-2">
                        {chap.title}
                      </h3>

                      <div className="flex justify-between items-center mt-4">
                        <div className="flex items-center gap-2">
                          <div className="w-2 h-2 rounded-full bg-[#e8ff47] shadow-[0_0_8px_#e8ff47]" />
                          <span className="text-xs font-mono text-[#888]">{chap.status}</span>
                        </div>
                        <div className="text-[10px] text-[#444] uppercase tracking-widest font-mono group-hover:text-[#888] transition-colors">
                          Pull to Open
                        </div>
                      </div>
                    </motion.div>
                  </Reorder.Item>
                );
              })}
            </Reorder.Group>

            {/* Holder Bottom Lip Cover for depth */}
            <div className="absolute bottom-0 left-0 right-0 h-16 bg-gradient-to-t from-[#0a0a0a] to-transparent pointer-events-none z-20" />
          </div>
        </div>

        {/* Right Side Empty Space for Floating Card (Visual Balance) */}
        <div className="hidden lg:block flex-1 h-[600px]" />
      </div>

      {/* Expanded Modal Overlay */}
      <AnimatePresence>
        {expandedId && expandedChapter && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center p-4 sm:p-6 lg:p-12 pointer-events-none"
          >
            {/* Backdrop Blur */}
            <motion.div
              className="absolute inset-0 bg-[#050505]/80 backdrop-blur-2xl pointer-events-auto"
              onClick={() => setExpandedId(null)}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
            >
              {/* Floating radial glow behind the card */}
              <motion.div
                animate={{ rotate: 360, scale: [1, 1.1, 1] }}
                transition={{ duration: 20, repeat: Infinity, ease: 'linear' }}
                className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[600px] bg-[#00f0ff]/10 rounded-full blur-[100px]"
              />
            </motion.div>

            {/* Expanded Card */}
            <motion.div
              layoutId={`card-container-${expandedChapter.id}`}
              className="relative w-full max-w-6xl bg-[#0c0c0c] border border-white/10 rounded-3xl overflow-hidden shadow-[0_30px_100px_rgba(0,0,0,1),0_0_0_1px_rgba(0,240,255,0.1)] flex flex-col lg:flex-row pointer-events-auto"
              transition={{ type: 'spring', damping: 30, stiffness: 200, mass: 1.2 }}
            >
              <button
                onClick={() => setExpandedId(null)}
                className="absolute top-4 right-4 lg:top-6 lg:right-6 w-10 h-10 rounded-full bg-black/50 backdrop-blur border border-white/10 flex items-center justify-center text-white hover:bg-white/10 hover:rotate-90 transition-all z-20 shadow-lg"
              >
                <svg
                  width="16"
                  height="16"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                >
                  <path d="M18 6L6 18M6 6l12 12" />
                </svg>
              </button>

              {/* Video Area (Left) */}
              <div className="w-full lg:w-3/5 bg-[#050505] relative aspect-video lg:aspect-auto min-h-[300px]">
                <DemoSimulator chapterId={expandedChapter.id} />

                {/* Video Overlays */}
                <div className="absolute inset-0 bg-gradient-to-t from-[#0c0c0c] via-transparent to-transparent lg:bg-gradient-to-r lg:from-transparent lg:via-[#0c0c0c]/50 lg:to-[#0c0c0c]" />

                <div className="absolute bottom-6 left-6 right-6 lg:left-8 lg:right-12 flex items-center gap-4 text-[#888] font-mono text-xs z-10">
                  <div className="w-10 h-10 rounded-full bg-white/10 backdrop-blur-md flex items-center justify-center text-white hover:bg-white/20 hover:scale-110 transition-all cursor-pointer border border-white/5">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                      <path d="M8 5v14l11-7z" />
                    </svg>
                  </div>
                  <div className="h-1.5 flex-1 bg-white/10 rounded-full overflow-hidden relative cursor-pointer group">
                    <div className="absolute inset-0 bg-white/20 opacity-0 group-hover:opacity-100 transition-opacity" />
                    <motion.div
                      initial={{ width: '0%' }}
                      animate={{ width: '100%' }}
                      transition={{ duration: 15, repeat: Infinity, ease: 'linear' }}
                      className="h-full bg-[#00f0ff] relative"
                    >
                      <div className="absolute right-0 top-1/2 -translate-y-1/2 w-2 h-2 bg-white rounded-full opacity-0 group-hover:opacity-100 shadow-[0_0_10px_#00f0ff]" />
                    </motion.div>
                  </div>
                  <span className="tabular-nums font-bold text-[#e5e5e5]">
                    {expandedChapter.time}
                  </span>
                </div>
              </div>

              {/* Info Area (Right) */}
              <div className="w-full lg:w-2/5 p-8 lg:p-12 flex flex-col justify-center relative bg-[#0c0c0c]">
                <motion.span
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: 0.2 }}
                  className="text-xs font-mono text-[#00f0ff] uppercase tracking-widest mb-4 inline-block"
                >
                  Classified File // {expandedChapter.id}
                </motion.span>

                <motion.h2
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: 0.3 }}
                  className="text-3xl lg:text-4xl font-bold text-white mb-6 leading-tight"
                >
                  {expandedChapter.title}
                </motion.h2>

                <motion.p
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: 0.4 }}
                  className="text-[#a8a8a8] leading-relaxed mb-8 font-light"
                >
                  {expandedChapter.desc}
                </motion.p>

                <motion.div
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: 0.5 }}
                  className="space-y-4 mb-10"
                >
                  {expandedChapter.points.map((pt, i) => (
                    <div key={i} className="flex items-center gap-4 text-sm text-[#e5e5e5]">
                      <div className="w-1.5 h-1.5 rounded-full bg-[#e8ff47] shadow-[0_0_8px_rgba(232,255,71,0.6)]" />
                      {pt}
                    </div>
                  ))}
                </motion.div>

                <motion.div
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: 0.6 }}
                  className="flex flex-col sm:flex-row items-stretch gap-4 mt-auto pt-8 border-t border-white/5"
                >
                  <button className="flex-1 py-3.5 rounded-xl bg-white text-black font-bold hover:bg-[#e5e5e5] transition-colors shadow-[0_0_20px_rgba(255,255,255,0.1)]">
                    Execute Demo
                  </button>
                  <button className="px-6 py-3.5 rounded-xl border border-white/10 text-white font-bold hover:bg-white/5 transition-colors">
                    Share
                  </button>
                </motion.div>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </section>
  );
}
