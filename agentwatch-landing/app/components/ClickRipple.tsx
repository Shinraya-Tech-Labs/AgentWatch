'use client';

import { useEffect } from 'react';

/**
 * Global click ripple — a soft cybernetic pulse at each pointerdown.
 * Pure DOM, GPU-accelerated. Skips clicks on form inputs.
 */
export default function ClickRipple() {
  useEffect(() => {
    const onPointerDown = (e: PointerEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && t.closest("input,textarea,[contenteditable='true']")) return;

      const ripple = document.createElement('span');
      ripple.className = 'click-ripple';
      ripple.style.left = `${e.clientX}px`;
      ripple.style.top = `${e.clientY}px`;
      document.body.appendChild(ripple);
      // Remove after animation
      setTimeout(() => ripple.remove(), 900);
    };

    window.addEventListener('pointerdown', onPointerDown, { passive: true });
    return () => window.removeEventListener('pointerdown', onPointerDown);
  }, []);

  return null;
}
