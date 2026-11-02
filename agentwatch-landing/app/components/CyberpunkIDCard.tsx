'use client';

import React, { useRef, useState, useEffect } from 'react';
import Image from 'next/image';
import { motion, useMotionValue, useSpring, useTransform } from 'framer-motion';

export type CreatorData = {
  name: string;
  role: string;
  bio: string;
  githubUrl: string;
  githubUsername: string;
  avatarUrl: string;
  clearanceLevel: string;
  idNumber: string;
  projects: string[];
  coreStack: string[];
  traits: string[];
  themeColor: string; // e.g. "#00f0ff"
  accentColor: string; // e.g. "#e8ff47"
};

export default function CyberpunkIDCard({ creator }: { creator: CreatorData }) {
  const cardRef = useRef<HTMLDivElement>(null);
  const [isFlipped, setIsFlipped] = useState(false);
  const [isHovered, setIsHovered] = useState(false);
  const [isMobile, setIsMobile] = useState(false);

  const mouseX = useMotionValue(0.5);
  const mouseY = useMotionValue(0.5);

  const springConfig = { damping: 25, stiffness: 300, mass: 0.5 };
  const smoothMouseX = useSpring(mouseX, springConfig);
  const smoothMouseY = useSpring(mouseY, springConfig);

  const rotateX = useTransform(smoothMouseY, [0, 1], [10, -10]);
  const rotateY = useTransform(smoothMouseX, [0, 1], [-10, 10]);

  const glareX = useTransform(smoothMouseX, [0, 1], [-100, 100]);
  const glareY = useTransform(smoothMouseY, [0, 1], [-100, 100]);

  useEffect(() => {
    const checkMobile = () => setIsMobile(window.innerWidth < 768);
    checkMobile();
    window.addEventListener('resize', checkMobile);
    return () => window.removeEventListener('resize', checkMobile);
  }, []);

  const handleMouseMove = (e: React.MouseEvent<HTMLDivElement>) => {
    if (isMobile || !cardRef.current) return;
    const rect = cardRef.current.getBoundingClientRect();
    const x = (e.clientX - rect.left) / rect.width;
    const y = (e.clientY - rect.top) / rect.height;
    mouseX.set(x);
    mouseY.set(y);
  };

  const handleMouseLeave = () => {
    setIsHovered(false);
    if (isMobile) return;
    mouseX.set(0.5);
    mouseY.set(0.5);
  };

  const handleMouseEnter = () => setIsHovered(true);

  const toggleFlip = (e: React.MouseEvent) => {
    if (
      (e.target as HTMLElement).closest('a') ||
      (e.target as HTMLElement).closest('button.no-flip')
    )
      return;
    setIsFlipped(!isFlipped);
  };

  return (
    <motion.div
      ref={cardRef}
      drag
      dragConstraints={{ left: 0, right: 0, top: 0, bottom: 0 }}
      dragElastic={0.2}
      onMouseMove={handleMouseMove}
      onMouseLeave={handleMouseLeave}
      onMouseEnter={handleMouseEnter}
      onClick={toggleFlip}
      whileDrag={{ scale: 1.03, cursor: 'grabbing' }}
      animate={isHovered ? { y: 0 } : { y: [-5, 5, -5], rotateZ: [-0.5, 0.5, -0.5] }}
      transition={
        isHovered
          ? { type: 'spring', stiffness: 300, damping: 20 }
          : {
              y: { duration: 4, repeat: Infinity, ease: 'easeInOut' },
              rotateZ: { duration: 6, repeat: Infinity, ease: 'easeInOut' },
            }
      }
      style={{
        rotateX: isMobile ? 0 : rotateX,
        rotateY: isMobile ? 0 : rotateY,
        transformStyle: 'preserve-3d',
      }}
      className="relative w-full max-w-[650px] h-[420px] rounded-[24px] cursor-grab group perspective-1000 z-20"
    >
      <div
        className="absolute -inset-[2px] rounded-[26px] opacity-40 blur-md group-hover:opacity-70 transition-opacity duration-500 z-0"
        style={{
          background: `linear-gradient(to top right, ${creator.themeColor}, ${creator.accentColor})`,
        }}
      />

      <motion.div
        animate={{ rotateY: isFlipped ? 180 : 0 }}
        transition={{ type: 'spring', stiffness: 100, damping: 15, mass: 1 }}
        className="w-full h-full relative z-10"
        style={{ transformStyle: 'preserve-3d' }}
      >
        <CardFront glareX={glareX} glareY={glareY} isMobile={isMobile} creator={creator} />
        <CardBack glareX={glareX} glareY={glareY} isMobile={isMobile} creator={creator} />
      </motion.div>
    </motion.div>
  );
}

const CardFront = ({
  glareX,
  glareY,
  isMobile,
  creator,
}: {
  glareX: any;
  glareY: any;
  isMobile: boolean;
  creator: CreatorData;
}) => {
  return (
    <div
      className="absolute inset-0 w-full h-full rounded-[24px] bg-[#0c0c0c]/90 backdrop-blur-xl border border-white/20 overflow-hidden"
      style={{ backfaceVisibility: 'hidden' }}
    >
      <BackgroundLayers themeColor={creator.themeColor} accentColor={creator.accentColor} />

      <div className="relative z-20 flex flex-col h-full p-8 text-white justify-between">
        <div className="flex justify-between items-start">
          <div className="flex flex-col">
            <h3 className="text-4xl font-black tracking-tight leading-none uppercase">
              {creator.name}
            </h3>
            <span
              style={{ color: creator.themeColor }}
              className="font-mono text-xs tracking-widest mt-1 uppercase"
            >
              {creator.role}
            </span>
          </div>

          <div
            style={{
              borderColor: `${creator.themeColor}4d`,
              backgroundColor: `${creator.themeColor}1a`,
              boxShadow: `0 0 15px ${creator.themeColor}33`,
            }}
            className="flex items-center gap-2 border px-4 py-2 rounded-full"
          >
            <motion.div
              animate={{ opacity: [1, 0.4, 1] }}
              transition={{ duration: 2, repeat: Infinity, ease: 'linear' }}
              style={{ backgroundColor: creator.themeColor }}
              className="w-2.5 h-2.5 rounded-full"
            />
            <span
              style={{ color: creator.themeColor }}
              className="text-[10px] font-mono font-bold tracking-widest uppercase"
            >
              {creator.clearanceLevel}
            </span>
          </div>
        </div>

        <div className="flex items-center gap-8 my-auto">
          <div className="relative w-36 h-36 shrink-0 group-hover:scale-105 transition-transform duration-500">
            <div
              style={{ borderColor: `${creator.accentColor}80` }}
              className="absolute inset-0 rounded-full border-2 border-dashed animate-spin-slow"
            />
            <div
              style={{ borderColor: `${creator.themeColor}4d` }}
              className="absolute -inset-1 rounded-full border border-solid animate-spin-slow-reverse"
            />
            <div className="w-full h-full rounded-full overflow-hidden border-2 border-[#111] relative z-10 bg-[#111]">
              <Image
                src={creator.avatarUrl}
                alt={creator.name}
                fill
                className="object-cover"
                unoptimized
              />
            </div>
          </div>

          <div className="flex-1">
            <p className="text-base text-[#a8a8a8] leading-relaxed font-light">{creator.bio}</p>
          </div>
        </div>

        <div className="flex justify-between items-end border-t border-white/10 pt-5">
          <div className="font-mono text-[11px] text-[#555] tracking-widest flex flex-col gap-1">
            <span>ID: {creator.idNumber}</span>
            <span>EXP: NEVER</span>
          </div>

          <div className="flex gap-4">
            <a
              href={creator.githubUrl}
              target="_blank"
              className="p-2.5 bg-white/5 border border-white/10 rounded-lg hover:bg-white/20 transition-colors hover:-translate-y-1 transform duration-200"
            >
              <svg
                width="20"
                height="20"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon>
              </svg>
            </a>
            <a
              href="#"
              className="p-2.5 bg-white/5 border border-white/10 rounded-lg hover:bg-white/20 transition-colors hover:-translate-y-1 transform duration-200"
            >
              <svg
                width="20"
                height="20"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="M17 8l4 4m0 0l-4 4m4-4H3" />
              </svg>
            </a>
          </div>
        </div>
      </div>

      {!isMobile && <GlareLayer glareX={glareX} glareY={glareY} />}
    </div>
  );
};

const CardBack = ({
  glareX,
  glareY,
  isMobile,
  creator,
}: {
  glareX: any;
  glareY: any;
  isMobile: boolean;
  creator: CreatorData;
}) => {
  return (
    <div
      className="absolute inset-0 w-full h-full rounded-[24px] bg-[#0c0c0c]/90 backdrop-blur-xl border overflow-hidden"
      style={{
        backfaceVisibility: 'hidden',
        transform: 'rotateY(180deg)',
        borderColor: `${creator.themeColor}4d`,
      }}
    >
      <BackgroundLayers themeColor={creator.themeColor} accentColor={creator.accentColor} />

      <div className="relative z-20 flex flex-col h-full p-8 text-white">
        <h4
          style={{ color: creator.themeColor }}
          className="font-mono text-xs uppercase tracking-widest mb-5 border-b border-white/10 pb-2"
        >
          Classified Data
        </h4>

        <div className="flex gap-8 h-full">
          <div className="flex-1 flex flex-col gap-5">
            <h5 className="text-sm font-bold uppercase tracking-wider text-[#888]">Core Stack</h5>
            <div className="flex flex-wrap gap-2.5">
              {creator.coreStack.map((tech) => (
                <span
                  key={tech}
                  className="px-2.5 py-1.5 bg-white/5 border border-white/10 rounded text-[10px] font-mono hover:bg-white/10 transition-colors cursor-default"
                >
                  {tech}
                </span>
              ))}
            </div>

            <h5 className="text-sm font-bold uppercase tracking-wider text-[#888] mt-2">
              Projects
            </h5>
            <ul className="text-sm space-y-3 text-[#a8a8a8]">
              {creator.projects.map((proj, i) => (
                <li key={i} className="flex items-center gap-2.5">
                  <span
                    style={{
                      backgroundColor: i % 2 === 0 ? creator.themeColor : creator.accentColor,
                    }}
                    className="w-2 h-2 rounded-full"
                  />{' '}
                  {proj}
                </li>
              ))}
            </ul>
          </div>

          <div className="flex-1 bg-black/60 border border-white/10 rounded-xl p-4 font-mono text-xs text-green-400 flex flex-col">
            <div className="flex items-center gap-2 mb-3 border-b border-white/10 pb-2">
              <div className="w-2.5 h-2.5 rounded-full bg-red-500" />
              <div className="w-2.5 h-2.5 rounded-full bg-yellow-500" />
              <div className="w-2.5 h-2.5 rounded-full bg-green-500" />
            </div>
            <div className="space-y-1.5 flex-1">
              <p>&gt; sys.get_traits()</p>
              {creator.traits.map((trait, i) => (
                <p key={i} className="text-[#a8a8a8] pl-2">
                  - {trait}
                </p>
              ))}
              <p className="mt-3 animate-pulse">&gt; _</p>
            </div>
            <div className="text-right text-[#555] text-[9px] mt-auto">TERMINAL V9.1</div>
          </div>
        </div>

        <div className="mt-auto pt-5 flex justify-between items-center border-t border-white/10">
          <span className="font-mono text-[10px] text-[#555] tracking-widest">TAP TO FLIP</span>
          <a
            href="mailto:contact@voidswift.com"
            style={{ color: creator.themeColor }}
            className="text-[11px] font-mono hover:underline"
          >
            contact@voidswift.com
          </a>
        </div>
      </div>

      {!isMobile && <GlareLayer glareX={glareX} glareY={glareY} />}
    </div>
  );
};

const BackgroundLayers = ({
  themeColor,
  accentColor,
}: {
  themeColor: string;
  accentColor: string;
}) => (
  <>
    <div
      className="absolute inset-0 opacity-20 z-0 pointer-events-none"
      style={{
        backgroundImage:
          'linear-gradient(rgba(255,255,255,0.1) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.1) 1px, transparent 1px)',
        backgroundSize: '20px 20px',
      }}
    />
    <div
      className="absolute inset-0 opacity-[0.03] z-0 pointer-events-none"
      style={{
        backgroundImage:
          'repeating-linear-gradient(45deg, #fff 0, #fff 1px, transparent 1px, transparent 4px)',
      }}
    />
    <div
      style={{ backgroundColor: themeColor }}
      className="absolute -bottom-20 -right-20 w-64 h-64 opacity-10 rounded-full blur-[80px] pointer-events-none"
    />
    <div
      style={{ backgroundColor: accentColor }}
      className="absolute -top-20 -left-20 w-64 h-64 opacity-[0.05] rounded-full blur-[80px] pointer-events-none"
    />
  </>
);

const GlareLayer = ({ glareX, glareY }: any) => {
  return (
    <motion.div
      className="absolute inset-0 z-50 pointer-events-none rounded-[24px]"
      style={{
        background: `radial-gradient(circle at center, rgba(255,255,255,0.15) 0%, rgba(255,255,255,0) 60%)`,
        x: glareX,
        y: glareY,
        mixBlendMode: 'overlay',
      }}
    />
  );
};
