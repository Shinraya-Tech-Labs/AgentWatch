'use client';

import { useEffect, useRef } from 'react';
import * as THREE from 'three';

/**
 * SITE-WIDE NEURAL BACKGROUND
 *
 * - Fixed, full-viewport canvas behind all content (transparent clear).
 * - Three intensity zones driven by scroll progress:
 *     hero (0)  : full intensity
 *     middle    : calmer
 *     deep      : ambient traces only
 * - Cursor projects into the scene; nodes within a radius brighten/scale
 *   and pull lines toward the cursor (no DOM cost — all on GPU shader inputs).
 * - Click emits a propagating wave: a ring of brightness travels through
 *   the network from the click point. Stack up to 4 simultaneous waves.
 */

const NODE_COUNT = 220;
const CONNECTION_THRESHOLD = 120;
const PARTICLE_COUNT = 80;
const MAX_WAVES = 4;
const WAVE_SPEED = 280; // world units / sec
const WAVE_BAND = 60; // thickness of the bright ring
const WAVE_LIFE = 2.0; // seconds

interface NodeData {
  velocity: THREE.Vector3;
  baseOpacity: number;
  pulseOffset: number;
}

interface ParticleData {
  fromIdx: number;
  toIdx: number;
  t: number;
  speed: number;
}

interface Wave {
  origin: THREE.Vector3;
  startTime: number;
}

export default function ThreeBackground() {
  const mountRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;

    // — Renderer: transparent, full viewport —
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(window.innerWidth, window.innerHeight);
    renderer.setClearColor(0x000000, 0); // transparent
    mount.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(
      60,
      window.innerWidth / window.innerHeight,
      0.1,
      2000,
    );
    camera.position.set(0, 0, 400);

    // — Cursor & scroll state —
    let mouseX = 0;
    let mouseY = 0;
    let cursorNDC = new THREE.Vector2(0, 0); // -1..1 normalized device coords
    let cursorWorld = new THREE.Vector3(0, 0, 0);
    let targetCameraX = 0;
    let targetCameraY = 0;
    let scrollProgress = 0;
    let intensity = 1;

    const onMouseMove = (e: MouseEvent) => {
      mouseX = (e.clientX / window.innerWidth - 0.5) * 2;
      mouseY = (e.clientY / window.innerHeight - 0.5) * 2;
      cursorNDC.set(mouseX, -mouseY);
    };
    window.addEventListener('mousemove', onMouseMove, { passive: true });

    const onScroll = () => {
      const max = Math.max(1, document.body.scrollHeight - window.innerHeight);
      scrollProgress = Math.min(1, Math.max(0, window.scrollY / max));
      // intensity curve: hero (1.0) → middle (0.55) → bottom (0.18)
      intensity = 1.0 - scrollProgress * 0.82;
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    onScroll();

    // — Click waves —
    const waves: Wave[] = [];
    const raycaster = new THREE.Raycaster();
    const interactionPlane = new THREE.Plane(new THREE.Vector3(0, 0, 1), 0);

    const projectCursorTo3D = (clientX: number, clientY: number) => {
      const ndc = new THREE.Vector2(
        (clientX / window.innerWidth) * 2 - 1,
        -((clientY / window.innerHeight) * 2 - 1),
      );
      raycaster.setFromCamera(ndc, camera);
      const target = new THREE.Vector3();
      raycaster.ray.intersectPlane(interactionPlane, target);
      return target;
    };

    const onPointerDown = (e: PointerEvent) => {
      // Skip if user clicked a form input or content-editable
      const t = e.target as HTMLElement | null;
      if (t && t.closest("input,textarea,[contenteditable='true']")) return;
      const origin = projectCursorTo3D(e.clientX, e.clientY);
      waves.push({ origin, startTime: performance.now() * 0.001 });
      if (waves.length > MAX_WAVES) waves.shift();
    };
    window.addEventListener('pointerdown', onPointerDown);

    // — Colors —
    const nodeColors = [
      new THREE.Color(0xe8ff47),
      new THREE.Color(0xbcd20e),
      new THREE.Color(0xffffff),
    ];

    // — Build nodes —
    const nodePositions: THREE.Vector3[] = [];
    const nodeMeshes: THREE.Mesh[] = [];
    const nodeData: NodeData[] = [];
    const nodeGeo = new THREE.SphereGeometry(1.5, 8, 8);

    for (let i = 0; i < NODE_COUNT; i++) {
      const pos = new THREE.Vector3(
        (Math.random() - 0.5) * 600,
        (Math.random() - 0.5) * 400,
        (Math.random() - 0.5) * 300,
      );
      nodePositions.push(pos);

      const color = nodeColors[Math.floor(Math.random() * nodeColors.length)];
      const opacity = 0.15 + Math.random() * 0.2;
      const mat = new THREE.MeshBasicMaterial({
        color,
        transparent: true,
        opacity,
      });
      const mesh = new THREE.Mesh(nodeGeo, mat);
      mesh.position.copy(pos);
      scene.add(mesh);
      nodeMeshes.push(mesh);

      nodeData.push({
        velocity: new THREE.Vector3(
          (Math.random() - 0.5) * 0.15,
          (Math.random() - 0.5) * 0.15,
          (Math.random() - 0.5) * 0.1,
        ),
        baseOpacity: opacity,
        pulseOffset: Math.random() * Math.PI * 2,
      });
    }

    // — Lines —
    const maxLines = 2000;
    const linePositionsArr = new Float32Array(maxLines * 6);
    const lineColorsArr = new Float32Array(maxLines * 6);
    const lineGeo = new THREE.BufferGeometry();
    lineGeo.setAttribute('position', new THREE.BufferAttribute(linePositionsArr, 3));
    lineGeo.setAttribute('color', new THREE.BufferAttribute(lineColorsArr, 3));
    const lineMat = new THREE.LineBasicMaterial({
      vertexColors: true,
      transparent: true,
      opacity: 1,
    });
    const lineSegments = new THREE.LineSegments(lineGeo, lineMat);
    scene.add(lineSegments);

    // — Cursor-following ambient particles (subtle field around cursor) —
    const cursorGlow = new THREE.Mesh(
      new THREE.SphereGeometry(8, 16, 16),
      new THREE.MeshBasicMaterial({ color: 0xe8ff47, transparent: true, opacity: 0.18 }),
    );
    scene.add(cursorGlow);

    // — Traveling particles —
    const particleGeo = new THREE.SphereGeometry(0.8, 4, 4);
    const particleMat = new THREE.MeshBasicMaterial({
      color: 0xe8ff47,
      transparent: true,
      opacity: 0.9,
    });
    const particleMeshes: THREE.Mesh[] = [];
    const particleData: ParticleData[] = [];

    for (let i = 0; i < PARTICLE_COUNT; i++) {
      const mesh = new THREE.Mesh(particleGeo, particleMat.clone());
      mesh.visible = false;
      scene.add(mesh);
      particleMeshes.push(mesh);
      particleData.push({
        fromIdx: Math.floor(Math.random() * NODE_COUNT),
        toIdx: Math.floor(Math.random() * NODE_COUNT),
        t: Math.random(),
        speed: 0.003 + Math.random() * 0.005,
      });
    }

    // — Animation —
    let frameId: number;
    let isVisible = true;

    const onVisibilityChange = () => {
      isVisible = !document.hidden;
    };
    document.addEventListener('visibilitychange', onVisibilityChange);

    const accentColor = new THREE.Color(0xe8ff47);
    const CURSOR_RADIUS = 110;
    const tmpV = new THREE.Vector3();

    const animate = () => {
      if (!isVisible) {
        frameId = requestAnimationFrame(animate);
        return;
      }

      const now = performance.now() * 0.001;

      // Camera parallax
      targetCameraX += (mouseX * 15 - targetCameraX) * 0.05;
      targetCameraY += (-mouseY * 10 - targetCameraY) * 0.05;
      camera.position.x += (targetCameraX - camera.position.x) * 0.05;
      camera.position.y += (targetCameraY - camera.position.y) * 0.05;
      camera.lookAt(0, 0, 0);

      // Project cursor to 3D plane (each frame, in case of camera shift)
      raycaster.setFromCamera(cursorNDC, camera);
      const hit = new THREE.Vector3();
      if (raycaster.ray.intersectPlane(interactionPlane, hit)) {
        cursorWorld.lerp(hit, 0.18);
      }
      cursorGlow.position.copy(cursorWorld);
      (cursorGlow.material as THREE.MeshBasicMaterial).opacity = 0.18 * intensity;

      // Update nodes
      for (let i = 0; i < NODE_COUNT; i++) {
        const pos = nodePositions[i];
        const data = nodeData[i];
        const mesh = nodeMeshes[i];

        pos.add(data.velocity);
        if (Math.abs(pos.x) > 310) data.velocity.x *= -1;
        if (Math.abs(pos.y) > 210) data.velocity.y *= -1;
        if (Math.abs(pos.z) > 160) data.velocity.z *= -1;
        mesh.position.copy(pos);

        const pulse = Math.sin(now * 1.5 + data.pulseOffset) * 0.15 + 0.85;

        // Cursor proximity boost
        const cursorDist = pos.distanceTo(cursorWorld);
        const cursorBoost =
          cursorDist < CURSOR_RADIUS ? 1 + (1 - cursorDist / CURSOR_RADIUS) * 1.6 : 1;

        // Click wave boost (sum over active waves)
        let waveBoost = 0;
        for (let w = 0; w < waves.length; w++) {
          const wave = waves[w];
          const age = now - wave.startTime;
          if (age > WAVE_LIFE) continue;
          const radius = age * WAVE_SPEED;
          const d = Math.abs(pos.distanceTo(wave.origin) - radius);
          if (d < WAVE_BAND) {
            const ringStrength = (1 - d / WAVE_BAND) * (1 - age / WAVE_LIFE);
            waveBoost += ringStrength * 2.4;
          }
        }

        const opacity = data.baseOpacity * pulse * cursorBoost * intensity + waveBoost * 0.4;
        (mesh.material as THREE.MeshBasicMaterial).opacity = Math.min(opacity, 1);

        // Subtle scale on proximity / wave
        const scale = Math.min(1 + (cursorBoost - 1) * 0.4 + waveBoost * 0.5, 2.2);
        mesh.scale.setScalar(scale);
      }

      // Prune dead waves
      while (waves.length && now - waves[0].startTime > WAVE_LIFE) waves.shift();

      // Build lines
      let lineCount = 0;
      const posAttr = lineGeo.getAttribute('position') as THREE.BufferAttribute;
      const colAttr = lineGeo.getAttribute('color') as THREE.BufferAttribute;
      const activeConnections: [number, number][] = [];

      for (let i = 0; i < NODE_COUNT && lineCount < maxLines; i++) {
        for (let j = i + 1; j < NODE_COUNT && lineCount < maxLines; j++) {
          const dist = nodePositions[i].distanceTo(nodePositions[j]);
          if (dist < CONNECTION_THRESHOLD) {
            let opacity = (1 - dist / CONNECTION_THRESHOLD) * 0.12 * intensity;

            // Cursor proximity highlight on lines whose midpoint is near cursor
            tmpV.copy(nodePositions[i]).add(nodePositions[j]).multiplyScalar(0.5);
            const md = tmpV.distanceTo(cursorWorld);
            if (md < CURSOR_RADIUS * 1.2) {
              opacity *= 1 + (1 - md / (CURSOR_RADIUS * 1.2)) * 2.5;
            }
            // Wave highlights on lines
            for (let w = 0; w < waves.length; w++) {
              const wave = waves[w];
              const age = now - wave.startTime;
              if (age > WAVE_LIFE) continue;
              const radius = age * WAVE_SPEED;
              const d = Math.abs(tmpV.distanceTo(wave.origin) - radius);
              if (d < WAVE_BAND) {
                opacity += (1 - d / WAVE_BAND) * (1 - age / WAVE_LIFE) * 0.6;
              }
            }
            opacity = Math.min(opacity, 0.9);

            const base = lineCount * 6;
            posAttr.array[base] = nodePositions[i].x;
            posAttr.array[base + 1] = nodePositions[i].y;
            posAttr.array[base + 2] = nodePositions[i].z;
            posAttr.array[base + 3] = nodePositions[j].x;
            posAttr.array[base + 4] = nodePositions[j].y;
            posAttr.array[base + 5] = nodePositions[j].z;

            const r = accentColor.r * opacity * 4;
            const g = accentColor.g * opacity * 4;
            const b = accentColor.b * opacity * 4;
            colAttr.array[base] = r;
            colAttr.array[base + 1] = g;
            colAttr.array[base + 2] = b;
            colAttr.array[base + 3] = r;
            colAttr.array[base + 4] = g;
            colAttr.array[base + 5] = b;

            activeConnections.push([i, j]);
            lineCount++;
          }
        }
      }

      lineGeo.setDrawRange(0, lineCount * 2);
      posAttr.needsUpdate = true;
      colAttr.needsUpdate = true;

      // Particles
      for (let i = 0; i < PARTICLE_COUNT; i++) {
        const pd = particleData[i];
        const mesh = particleMeshes[i];
        pd.t += pd.speed;
        if (pd.t > 1) {
          pd.t = 0;
          if (activeConnections.length > 0) {
            const conn = activeConnections[Math.floor(Math.random() * activeConnections.length)];
            pd.fromIdx = conn[0];
            pd.toIdx = conn[1];
          }
        }
        const from = nodePositions[pd.fromIdx];
        const to = nodePositions[pd.toIdx];
        if (from && to) {
          mesh.position.lerpVectors(from, to, pd.t);
          mesh.visible = true;
          (mesh.material as THREE.MeshBasicMaterial).opacity = 0.9 * intensity;
        }
      }

      // Global canvas opacity also reflects intensity (cheap fade)
      renderer.domElement.style.opacity = `${0.35 + intensity * 0.65}`;

      renderer.render(scene, camera);
      frameId = requestAnimationFrame(animate);
    };

    animate();

    // Resize
    const onResize = () => {
      camera.aspect = window.innerWidth / window.innerHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(window.innerWidth, window.innerHeight);
    };
    window.addEventListener('resize', onResize);

    return () => {
      cancelAnimationFrame(frameId);
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('resize', onResize);
      window.removeEventListener('scroll', onScroll);
      window.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('visibilitychange', onVisibilityChange);
      renderer.dispose();
      if (mount.contains(renderer.domElement)) {
        mount.removeChild(renderer.domElement);
      }
    };
  }, []);

  return (
    <div
      ref={mountRef}
      className="fixed inset-0 z-0 pointer-events-none"
      style={{ width: '100vw', height: '100vh' }}
      aria-hidden="true"
    />
  );
}
