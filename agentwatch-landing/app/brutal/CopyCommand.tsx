'use client';

import { useState } from 'react';

export default function CopyCommand({ lines }: { lines: string[] }) {
  const [copied, setCopied] = useState(false);
  const text = lines.join('\n');
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      setCopied(false);
    }
  };
  return (
    <div className="flex items-stretch border-[3px] border-black bg-black text-left shadow-[6px_6px_0_0_#000]">
      <pre className="flex-1 overflow-x-auto px-4 py-3 font-mono text-sm leading-7 text-[#8CFF98] sm:text-base">
        {lines.map((l) => (
          <div key={l}>
            <span aria-hidden className="select-none text-[#FFE45E]">
              ${' '}
            </span>
            {l}
          </div>
        ))}
      </pre>
      <button
        type="button"
        onClick={copy}
        aria-label={copied ? 'Copied' : 'Copy the install commands'}
        className="border-l-[3px] border-black bg-[#FFE45E] px-4 font-display text-sm uppercase text-black hover:bg-[#FF8FD8] focus-visible:outline focus-visible:outline-4 focus-visible:-outline-offset-4 focus-visible:outline-black"
      >
        {copied ? 'Copied!' : 'Copy'}
      </button>
      <span role="status" className="sr-only">
        {copied ? 'Install commands copied to the clipboard' : ''}
      </span>
    </div>
  );
}
