'use client';

import React from 'react';
import CyberpunkIDCard from './CyberpunkIDCard';

export default function AboutCreator() {
  return (
    <section className="relative z-10 py-32 px-6 w-full flex flex-col items-center justify-center border-t border-white/5 overflow-hidden min-h-[800px] perspective-1000">
      <div className="text-center mb-16">
        <h2 className="text-3xl md:text-4xl font-bold tracking-tight text-white mb-4">
          Meet the Creator
        </h2>
        <div className="w-12 h-1 bg-gradient-to-r from-[#00f0ff] to-[#e8ff47] mx-auto rounded-full" />
      </div>

      <CyberpunkIDCard
        creator={{
          name: 'Sree Revanth',
          role: 'Founder @ AgentWatch',
          bio: 'AI/ML Engineer focused on autonomous systems, open-source software, and full-stack development. Building AgentWatch to stop autonomous AI systems from causing catastrophic failures.',
          githubUrl: 'https://github.com/sreerevanth',
          githubUsername: 'sreerevanth',
          avatarUrl: 'https://github.com/sreerevanth.png',
          clearanceLevel: 'Clearance A7',
          idNumber: '894-X-9921',
          projects: ['AgentWatch', 'RepoPilot'],
          coreStack: ['Python', 'TypeScript', 'ReactJS', 'NextJS', 'NodeJS', 'AI Agents', 'RAG'],
          traits: ['AI Observability', 'Multi-Model Orchestration', 'Algo-Trading'],
          themeColor: '#00f0ff',
          accentColor: '#e8ff47',
        }}
      />
    </section>
  );
}
