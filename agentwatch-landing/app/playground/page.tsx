'use client';

import { useState, useMemo } from 'react';
import Link from 'next/link';
import Navbar from '../components/Navbar';
import Footer from '../components/Footer';

export default function PlaygroundPage() {
  const [toolName, setToolName] = useState('execute_sql');
  const [toolArgs, setToolArgs] = useState('{"query": "DROP TABLE users;"}');
  const [verdict, setVerdict] = useState<'VETO' | 'ALLOW'>('VETO');
  const [cbState, setCbState] = useState('TRIPPED (OPEN)');
  const [confidence, setConfidence] = useState(0.1);
  const [merkleRoot, setMerkleRoot] = useState('0x9661f089d02af353...');
  const [logs, setLogs] = useState<
    Array<{ time: string; tag: string; msg: string; isVeto: boolean }>
  >([
    {
      time: '00:00:01',
      tag: 'INIT',
      msg: 'Panopti Sentinel Pre-Execution Runtime Hypervisor online.',
      isVeto: false,
    },
    {
      time: '00:00:02',
      tag: 'VETO',
      msg: "Pre-execution interceptor blocked 'execute_sql' (DROP TABLE users;) - Invariant breach!",
      isVeto: true,
    },
  ]);

  const [filterMode, setFilterMode] = useState<'ALL' | 'VETO' | 'ALLOW'>('ALL');
  const [searchQuery, setSearchQuery] = useState('');

  const filteredLogs = useMemo(() => {
    return logs.filter((log) => {
      const matchesFilter =
        filterMode === 'ALL' ||
        (filterMode === 'VETO' && (log.isVeto || log.tag === 'VETO')) ||
        (filterMode === 'ALLOW' && (log.tag === 'ALLOW' || (!log.isVeto && log.tag !== 'INIT')));

      const q = searchQuery.trim().toLowerCase();
      const matchesSearch =
        !q ||
        log.msg.toLowerCase().includes(q) ||
        log.tag.toLowerCase().includes(q) ||
        log.time.toLowerCase().includes(q);

      return matchesFilter && matchesSearch;
    });
  }, [logs, filterMode, searchQuery]);

  const scenarios = [
    {
      name: '💥 Adversarial SQL Injection (DROP TABLE)',
      tag: 'HIGH RISK',
      tool: 'execute_sql',
      args: '{"query": "DROP TABLE users;"}',
    },
    {
      name: '🔄 Tool Oscillation & Infinite Hammering',
      tag: 'LOOP TRAP',
      tool: 'sync_customer_data',
      args: '{"batch_id": 994, "retry_count": 8}',
    },
    {
      name: '⚠️ Unverified Financial State Mutation',
      tag: 'INVARIANT BREACH',
      tool: 'transfer_funds',
      args: '{"recipient": "0xAttacker", "amount_usd": 500000}',
    },
    {
      name: '✅ Nominal Safe Read Query',
      tag: 'BENIGN',
      tool: 'fetch_catalog',
      args: '{"category": "ai_tools", "limit": 10}',
    },
  ];

  const handleIntercept = (overrideTool?: string, overrideArgs?: string) => {
    const t = overrideTool ?? toolName;
    const a = overrideArgs ?? toolArgs;
    const isDestructive =
      /drop\s+table|truncate|delete\s+from|0xattacker|retry_count":\s*[5-9]/i.test(a);
    const now = new Date().toTimeString().split(' ')[0];
    const newHash = '0x' + Math.random().toString(16).substring(2, 10) + '...';

    if (isDestructive) {
      setVerdict('VETO');
      setCbState('TRIPPED (OPEN)');
      setConfidence(0.1);
      setMerkleRoot(newHash);
      setLogs((prev) => [
        ...prev,
        {
          time: now,
          tag: 'VETO',
          msg: `Blocked proposed tool '${t}' before execution. Invariant breach detected!`,
          isVeto: true,
        },
      ]);
    } else {
      setVerdict('ALLOW');
      setCbState('CLOSED');
      setConfidence(0.98);
      setMerkleRoot(newHash);
      setLogs((prev) => [
        ...prev,
        {
          time: now,
          tag: 'ALLOW',
          msg: `Approved proposed tool '${t}'. All temporal contracts and confidence floors satisfied.`,
          isVeto: false,
        },
      ]);
    }
  };

  return (
    <div className="min-h-screen bg-[#070b14] text-[#f0f4f8] font-sans antialiased selection:bg-[#00f0ff]/30">
      <Navbar />
      <main className="max-w-6xl mx-auto px-4 sm:px-6 pt-32 pb-20">
        <div className="mb-10 text-center">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full text-xs font-mono font-bold tracking-widest text-[#00f0ff] bg-[#00f0ff]/10 border border-[#00f0ff]/20 uppercase mb-4">
            Interactive Hypervisor Playground
          </div>
          <h1 className="text-4xl sm:text-5xl font-black tracking-tight text-white mb-4">
            Test Panopti’s <span className="text-[#00f0ff]">Active Intercept</span>
          </h1>
          <p className="text-base sm:text-lg text-[#8b9bb4] max-w-2xl mx-auto">
            Simulate malicious agent tool calls, adversarial prompt injections, and oscillation
            traps. Watch Panopti veto mutations before they reach production.
          </p>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
          {/* Controls Column */}
          <div className="bg-[#0e1626] border border-[#1d2a44] rounded-2xl p-6 sm:p-8 shadow-2xl flex flex-col justify-between">
            <div>
              <div className="text-xs font-mono font-bold tracking-wider text-[#00f0ff] uppercase mb-4">
                1. Select Attack Vector
              </div>

              <div className="space-y-3 mb-6">
                {scenarios.map((sc, i) => (
                  <button
                    key={i}
                    onClick={() => {
                      setToolName(sc.tool);
                      setToolArgs(sc.args);
                      handleIntercept(sc.tool, sc.args);
                    }}
                    className="w-full text-left p-3.5 rounded-xl border border-[#233352] bg-[#121c30] hover:border-[#00f0ff] hover:bg-[#182540] transition-all flex items-center justify-between group"
                  >
                    <span className="text-sm font-medium text-white group-hover:text-[#00f0ff] transition-colors">
                      {sc.name}
                    </span>
                    <span
                      className={`text-[10px] font-bold px-2 py-0.5 rounded border ${
                        sc.tag === 'BENIGN'
                          ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'
                          : 'bg-rose-500/10 text-rose-400 border-rose-500/30'
                      }`}
                    >
                      {sc.tag}
                    </span>
                  </button>
                ))}
              </div>

              <div className="space-y-4">
                <div>
                  <label className="block text-xs font-mono font-semibold text-[#8b9bb4] uppercase mb-2">
                    Proposed Tool Action
                  </label>
                  <input
                    type="text"
                    value={toolName}
                    onChange={(e) => setToolName(e.target.value)}
                    className="w-full bg-[#080d1a] border border-[#233352] focus:border-[#00f0ff] rounded-xl px-4 py-2.5 text-sm font-mono text-white outline-none transition-colors"
                  />
                </div>

                <div>
                  <label className="block text-xs font-mono font-semibold text-[#8b9bb4] uppercase mb-2">
                    Tool Payload Arguments (JSON)
                  </label>
                  <textarea
                    rows={3}
                    value={toolArgs}
                    onChange={(e) => setToolArgs(e.target.value)}
                    className="w-full bg-[#080d1a] border border-[#233352] focus:border-[#00f0ff] rounded-xl px-4 py-2.5 text-sm font-mono text-white outline-none transition-colors"
                  />
                </div>
              </div>
            </div>

            <button
              onClick={() => handleIntercept()}
              className="mt-6 w-full py-4 rounded-xl font-bold uppercase tracking-wider text-sm bg-gradient-to-r from-[#00f0ff] to-[#00a8ff] text-[#050a14] hover:opacity-90 active:scale-[0.99] transition-all shadow-[0_0_25px_rgba(0,240,255,0.3)]"
            >
              Intercept & Verify
            </button>
          </div>

          {/* Telemetry Column */}
          <div className="bg-[#0e1626] border border-[#1d2a44] rounded-2xl p-6 sm:p-8 shadow-2xl flex flex-col justify-between">
            <div>
              <div className="flex items-center justify-between mb-4">
                <span className="text-xs font-mono font-bold tracking-wider text-[#00f0ff] uppercase">
                  2. Hypervisor Telemetry
                </span>
                <span className="text-xs font-mono text-emerald-400">⚡ 0.01ms overhead</span>
              </div>

              {/* Verdict Banner */}
              <div
                className={`py-4 px-6 rounded-xl font-bold text-center tracking-wide text-lg border transition-all mb-6 ${
                  verdict === 'VETO'
                    ? 'bg-rose-500/15 text-rose-400 border-rose-500/40 shadow-[0_0_30px_rgba(244,63,94,0.2)] animate-pulse'
                    : 'bg-emerald-500/15 text-emerald-400 border-emerald-500/40 shadow-[0_0_30px_rgba(16,185,129,0.2)]'
                }`}
              >
                {verdict === 'VETO'
                  ? '🛑 PRE-EXECUTION VETO ENFORCED'
                  : '✅ PRE-EXECUTION APPROVED'}
              </div>

              {/* Stats Row */}
              <div className="grid grid-cols-3 gap-3 mb-6">
                <div className="bg-[#080d1a] border border-[#1d2a44] p-3 rounded-xl text-center">
                  <div className="text-[10px] font-mono text-[#8b9bb4] uppercase mb-1">
                    Circuit Breaker
                  </div>
                  <div
                    className={`font-mono text-xs font-bold ${verdict === 'VETO' ? 'text-rose-400' : 'text-emerald-400'}`}
                  >
                    {cbState}
                  </div>
                </div>
                <div className="bg-[#080d1a] border border-[#1d2a44] p-3 rounded-xl text-center">
                  <div className="text-[10px] font-mono text-[#8b9bb4] uppercase mb-1">
                    Confidence Floor
                  </div>
                  <div
                    className={`font-mono text-sm font-bold ${confidence < 0.4 ? 'text-rose-400' : 'text-emerald-400'}`}
                  >
                    {confidence.toFixed(2)}
                  </div>
                </div>
                <div className="bg-[#080d1a] border border-[#1d2a44] p-3 rounded-xl text-center">
                  <div className="text-[10px] font-mono text-[#8b9bb4] uppercase mb-1">
                    Merkle Witness
                  </div>
                  <div className="font-mono text-xs font-bold text-[#00f0ff] truncate">
                    {merkleRoot}
                  </div>
                </div>
              </div>

              {/* Audit Console Header & Controls */}
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-mono font-semibold text-[#8b9bb4] uppercase">
                  Cryptographic Witness Audit Stream
                </span>
                <span className="text-[11px] font-mono text-[#4e678e]">
                  Showing {filteredLogs.length} / {logs.length} events
                </span>
              </div>

              {/* Real-time Filter & Search Bar */}
              <div className="flex flex-col sm:flex-row gap-2 mb-3">
                <div className="relative flex-1">
                  <input
                    type="text"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    placeholder="Filter by tool or keyword..."
                    className="w-full bg-[#060911] border border-[#1f293d] focus:border-[#00f0ff] rounded-lg px-3 py-1.5 pr-7 text-xs font-mono text-white placeholder-[#4e678e] outline-none transition-colors"
                  />
                  {searchQuery && (
                    <button
                      onClick={() => setSearchQuery('')}
                      className="absolute right-2 top-1/2 -translate-y-1/2 text-[#4e678e] hover:text-white text-xs px-1"
                      title="Clear search"
                    >
                      ✕
                    </button>
                  )}
                </div>
                <div className="flex gap-1.5" role="tablist">
                  <button
                    onClick={() => setFilterMode('ALL')}
                    className={`flex-1 sm:flex-none px-2.5 py-1.5 rounded-lg text-xs font-mono font-semibold transition-all uppercase tracking-wider ${
                      filterMode === 'ALL'
                        ? 'bg-[#00f0ff]/15 text-[#00f0ff] border border-[#00f0ff]/40 shadow-[0_0_10px_rgba(0,240,255,0.2)]'
                        : 'bg-[#080d1a] text-[#8b9bb4] border border-[#1d2a44] hover:text-white hover:border-[#283859]'
                    }`}
                  >
                    All
                  </button>
                  <button
                    onClick={() => setFilterMode('VETO')}
                    className={`flex-1 sm:flex-none px-2.5 py-1.5 rounded-lg text-xs font-mono font-semibold transition-all uppercase tracking-wider ${
                      filterMode === 'VETO'
                        ? 'bg-rose-500/15 text-rose-400 border border-rose-500/40 shadow-[0_0_10px_rgba(244,63,94,0.2)]'
                        : 'bg-[#080d1a] text-[#8b9bb4] border border-[#1d2a44] hover:text-rose-300 hover:border-[#283859]'
                    }`}
                  >
                    Vetoes Only
                  </button>
                  <button
                    onClick={() => setFilterMode('ALLOW')}
                    className={`flex-1 sm:flex-none px-2.5 py-1.5 rounded-lg text-xs font-mono font-semibold transition-all uppercase tracking-wider ${
                      filterMode === 'ALLOW'
                        ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/40 shadow-[0_0_10px_rgba(16,185,129,0.2)]'
                        : 'bg-[#080d1a] text-[#8b9bb4] border border-[#1d2a44] hover:text-emerald-300 hover:border-[#283859]'
                    }`}
                  >
                    Approved Only
                  </button>
                </div>
              </div>

              {/* Console Stream */}
              <div className="bg-[#060911] border border-[#162033] rounded-xl p-4 font-mono text-xs h-48 overflow-y-auto space-y-2">
                {filteredLogs.length > 0 ? (
                  filteredLogs.map((log, idx) => (
                    <div key={idx} className="flex gap-2 leading-relaxed">
                      <span className="text-[#4e678e]">[{log.time}]</span>
                      <span
                        className={
                          log.isVeto ? 'text-rose-400 font-bold' : 'text-emerald-400 font-bold'
                        }
                      >
                        [{log.tag}]
                      </span>
                      <span className="text-[#c3d4ec]">{log.msg}</span>
                    </div>
                  ))
                ) : (
                  <div className="h-full flex items-center justify-center text-[#4e678e] italic text-xs py-8">
                    ⚡ No audit events matching current filter or search criteria.
                  </div>
                )}
              </div>
            </div>

            <div className="mt-6 pt-4 border-t border-[#1d2a44] flex items-center justify-between text-xs text-[#8b9bb4]">
              <span>Formal Logic: Metric Temporal Logic (MTL)</span>
              <span className="text-emerald-400">Zero DB Mutations</span>
            </div>
          </div>
        </div>
      </main>
      <Footer />
    </div>
  );
}
