'use client';

import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';

export default function CustomCursor() {
  const [mousePosition, setMousePosition] = useState({ x: -100, y: -100 });
  const [isHovering, setIsHovering] = useState(false);
  const [isClicking, setIsClicking] = useState(false);
  const [isVisible, setIsVisible] = useState(false);

  useEffect(() => {
    // Only run on non-touch devices
    if (window.matchMedia('(pointer: coarse)').matches) return;

    const updateMousePosition = (e: MouseEvent) => {
      setMousePosition({ x: e.clientX, y: e.clientY });
      if (!isVisible) setIsVisible(true);
    };

    const handleMouseOver = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      // Traverse up to check if a parent is clickable
      let current: HTMLElement | null = target;
      let clickable = false;

      while (current && current !== document.body) {
        if (
          window.getComputedStyle(current).cursor === 'pointer' ||
          current.tagName.toLowerCase() === 'a' ||
          current.tagName.toLowerCase() === 'button' ||
          current.onclick !== null
        ) {
          clickable = true;
          break;
        }
        current = current.parentElement;
      }

      setIsHovering(clickable);
    };

    const handleMouseDown = () => setIsClicking(true);
    const handleMouseUp = () => setIsClicking(false);
    const handleMouseLeave = () => setIsVisible(false);
    const handleMouseEnter = () => setIsVisible(true);

    window.addEventListener('mousemove', updateMousePosition);
    window.addEventListener('mouseover', handleMouseOver);
    window.addEventListener('mousedown', handleMouseDown);
    window.addEventListener('mouseup', handleMouseUp);
    document.body.addEventListener('mouseleave', handleMouseLeave);
    document.body.addEventListener('mouseenter', handleMouseEnter);

    // Hide default cursor globally
    const style = document.createElement('style');
    style.innerHTML = `
      * { cursor: none !important; }
    `;
    document.head.appendChild(style);

    return () => {
      window.removeEventListener('mousemove', updateMousePosition);
      window.removeEventListener('mouseover', handleMouseOver);
      window.removeEventListener('mousedown', handleMouseDown);
      window.removeEventListener('mouseup', handleMouseUp);
      document.body.removeEventListener('mouseleave', handleMouseLeave);
      document.body.removeEventListener('mouseenter', handleMouseEnter);
      document.head.removeChild(style);
    };
  }, [isVisible]);

  // Hide on mobile/touch devices entirely
  if (typeof window !== 'undefined' && window.matchMedia('(pointer: coarse)').matches) {
    return null;
  }

  return (
    <>
      {/* Trailing glow/ring */}
      <motion.div
        className="fixed top-0 left-0 w-12 h-12 rounded-full border border-[#00f0ff]/40 pointer-events-none z-[9998] mix-blend-screen"
        animate={{
          x: mousePosition.x - 24,
          y: mousePosition.y - 24,
          scale: isClicking ? 0.8 : isHovering ? 1.5 : 1,
          opacity: isVisible ? 1 : 0,
          backgroundColor: isHovering ? 'rgba(0, 240, 255, 0.05)' : 'rgba(0,0,0,0)',
          borderColor: isHovering ? 'rgba(232, 255, 71, 0.6)' : 'rgba(0, 240, 255, 0.4)',
        }}
        transition={{ type: 'spring', stiffness: 100, damping: 20, mass: 0.5 }}
      />

      {/* Generated Cursor Image (Center Pointer) */}
      <motion.img
        src="/cursor.jpg"
        alt="custom cursor"
        className="fixed top-0 left-0 w-8 h-8 pointer-events-none z-[9999] mix-blend-screen"
        animate={{
          x: mousePosition.x - 16,
          y: mousePosition.y - 16,
          scale: isClicking ? 0.7 : isHovering ? 0.9 : 1,
          opacity: isVisible ? 1 : 0,
          rotate: isHovering ? 45 : 0,
        }}
        transition={{ type: 'spring', stiffness: 500, damping: 28, mass: 0.1 }}
      />
    </>
  );
}
