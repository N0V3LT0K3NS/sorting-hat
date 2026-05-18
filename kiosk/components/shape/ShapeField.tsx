"use client";

import { Canvas, useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import styles from "../../app/shape-interface.module.css";
import { usePrefersReducedMotion } from "./usePrefersReducedMotion";

type VisualPhase =
  | "idle"
  | "connecting"
  | "listening"
  | "thinking"
  | "speaking"
  | "generating"
  | "reveal"
  | "error";

interface ShapeFieldProps {
  phase: VisualPhase;
  inputLevel?: number;
  outputLevel?: number;
  className?: string;
}

const PHASE_LABEL: Record<VisualPhase, string> = {
  idle: "awaiting contact",
  connecting: "opening room",
  listening: "listening",
  thinking: "reading signal",
  speaking: "speaking",
  generating: "rendering portrait",
  reveal: "shape resolved",
  error: "soft failure",
};

export function ShapeField({
  phase,
  inputLevel = 0,
  outputLevel = 0,
  className,
}: ShapeFieldProps) {
  const reducedMotion = usePrefersReducedMotion();

  return (
    <div className={`${styles.shapeField} ${className ?? ""}`}>
      <div className={styles.fieldChrome} aria-hidden>
        <span>SORTING HAT</span>
        <span>{PHASE_LABEL[phase]}</span>
      </div>
      <Canvas
        className={styles.canvas}
        camera={{ position: [0, 0, 4.2], fov: 38 }}
        dpr={[1, 1.5]}
        gl={{ antialias: true, alpha: true, powerPreference: "high-performance" }}
      >
        <color attach="background" args={["#000000"]} />
        <ambientLight intensity={0.82} color="#f5ece2" />
        <directionalLight position={[3.2, 3.8, 4.5]} intensity={2.3} color="#fff1df" />
        <directionalLight position={[-4, -1.2, 2.2]} intensity={0.62} color="#c85d38" />
        <pointLight position={[0, 0, 2.2]} intensity={1.2} color="#e77245" />
        <VoiceShape
          phase={phase}
          inputLevel={inputLevel}
          outputLevel={outputLevel}
          reducedMotion={reducedMotion}
        />
      </Canvas>
      <div className={styles.fieldGrid} aria-hidden />
    </div>
  );
}

function VoiceShape({
  phase,
  inputLevel,
  outputLevel,
  reducedMotion,
}: {
  phase: VisualPhase;
  inputLevel: number;
  outputLevel: number;
  reducedMotion: boolean;
}) {
  const groupRef = useRef<THREE.Group>(null);
  const meshRef = useRef<THREE.Mesh>(null);
  const haloRef = useRef<THREE.Mesh>(null);
  const dustRef = useRef<THREE.Points>(null);
  const materialRef = useRef<THREE.MeshPhysicalMaterial>(null);
  const targetScaleRef = useRef(new THREE.Vector3(1, 1, 1));
  const phaseRef = useRef(phase);
  const levelsRef = useRef({ input: inputLevel, output: outputLevel });

  useEffect(() => {
    phaseRef.current = phase;
  }, [phase]);

  useEffect(() => {
    levelsRef.current = { input: inputLevel, output: outputLevel };
  }, [inputLevel, outputLevel]);

  const geometry = useMemo(() => {
    const geo = new THREE.SphereGeometry(1, 96, 64);
    geo.computeVertexNormals();
    return geo;
  }, []);

  const basePositions = useMemo(() => {
    const pos = geometry.attributes.position as THREE.BufferAttribute;
    return new Float32Array(pos.array as Float32Array);
  }, [geometry]);

  const directions = useMemo(() => {
    const dirs = new Float32Array(basePositions.length);
    const v = new THREE.Vector3();
    for (let i = 0; i < basePositions.length; i += 3) {
      v.set(basePositions[i], basePositions[i + 1], basePositions[i + 2]).normalize();
      dirs[i] = v.x;
      dirs[i + 1] = v.y;
      dirs[i + 2] = v.z;
    }
    return dirs;
  }, [basePositions]);

  const dustGeometry = useMemo(() => {
    const count = 720;
    const positions = new Float32Array(count * 3);
    const offsets = new Float32Array(count * 3);
    const v = new THREE.Vector3();
    for (let i = 0; i < count; i++) {
      v.set(
        Math.random() * 2 - 1,
        Math.random() * 2 - 1,
        Math.random() * 2 - 1,
      ).normalize();
      const radius = 1.08 + Math.random() * 0.34;
      positions[i * 3] = v.x * radius;
      positions[i * 3 + 1] = v.y * radius;
      positions[i * 3 + 2] = v.z * radius;
      offsets[i * 3] = (Math.random() - 0.5) * 0.32;
      offsets[i * 3 + 1] = (Math.random() - 0.5) * 0.32;
      offsets[i * 3 + 2] = (Math.random() - 0.5) * 0.32;
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geo.setAttribute("seedOffset", new THREE.BufferAttribute(offsets, 3));
    return geo;
  }, []);

  const dustBasePositions = useMemo(() => {
    const pos = dustGeometry.attributes.position as THREE.BufferAttribute;
    return new Float32Array(pos.array as Float32Array);
  }, [dustGeometry]);

  useEffect(() => {
    return () => {
      geometry.dispose();
      dustGeometry.dispose();
    };
  }, [dustGeometry, geometry]);

  useFrame(({ clock }, delta) => {
    const time = clock.elapsedTime;
    const phaseName = phaseRef.current;
    const input = reducedMotion ? 0 : damp(levelsRef.current.input, 0.08);
    const output = reducedMotion ? 0 : damp(levelsRef.current.output, 0.08);
    const active = Math.max(input, output);
    const speaking = phaseName === "speaking";
    const listening = phaseName === "listening";
    const generating = phaseName === "generating";
    const reveal = phaseName === "reveal";
    const error = phaseName === "error";

    if (groupRef.current) {
      const targetScale =
        1 +
        (speaking ? 0.08 : 0) +
        (listening ? -0.025 : 0) +
        (generating ? 0.035 : 0) +
        (reveal ? 0.12 : 0) -
        (error ? 0.05 : 0);
      targetScaleRef.current.set(targetScale, targetScale, targetScale);
      groupRef.current.scale.lerp(
        targetScaleRef.current,
        Math.min(delta * 2.5, 0.12),
      );
      if (!reducedMotion) {
        groupRef.current.rotation.y += delta * (0.08 + output * 0.22);
        groupRef.current.rotation.x = Math.sin(time * 0.18) * 0.06;
        groupRef.current.rotation.z = Math.cos(time * 0.13) * 0.045;
      }
    }

    if (meshRef.current) {
      const pos = meshRef.current.geometry.attributes.position as THREE.BufferAttribute;
      for (let i = 0; i < pos.count; i++) {
        const ix = i * 3;
        const nx = directions[ix];
        const ny = directions[ix + 1];
        const nz = directions[ix + 2];
        const lobe =
          0.11 * Math.sin(nx * 3.2 + time * 0.5) +
          0.07 * Math.cos(ny * 4.1 - time * 0.36) +
          0.06 * Math.sin((nx + nz) * 5.4 + time * 0.22);
        const breath = reducedMotion
          ? 0
          : Math.sin(time * 0.9 + nx * 1.7 + ny * 0.8) * 0.025;
        const inputDimple =
          input *
          (-0.12 + 0.08 * Math.sin(nx * 5.1 + ny * 2.2 + time * 8.4));
        const outputBloom =
          output *
          (0.16 +
            0.12 * Math.sin(ny * 4.2 + nz * 3.4 + time * 7.2) +
            0.045 * Math.sin(nx * 10.0 + time * 18.0));
        const thinkingRipple =
          phaseName === "thinking"
            ? 0.035 * Math.sin((nx + ny + nz) * 10.0 + time * 4.0)
            : 0;
        const generatePull = generating
          ? 0.04 * Math.sin(Math.atan2(nz, nx) * 4.0 + time * 1.2)
          : 0;
        const revealBloom = reveal ? 0.12 * Math.max(0, nz + 0.15) : 0;
        const errorTighten = error ? -0.08 : 0;
        const scale =
          1.0 +
          lobe * 0.44 +
          breath +
          inputDimple +
          outputBloom +
          thinkingRipple +
          generatePull +
          revealBloom +
          errorTighten;
        pos.setXYZ(i, nx * scale, ny * scale, nz * scale);
      }
      pos.needsUpdate = true;
      meshRef.current.geometry.computeVertexNormals();
    }

    if (materialRef.current) {
      const mat = materialRef.current;
      mat.emissiveIntensity = 0.06 + active * 0.34 + (speaking ? 0.08 : 0);
      mat.roughness = THREE.MathUtils.lerp(mat.roughness, speaking ? 0.16 : 0.24, 0.06);
      mat.clearcoat = THREE.MathUtils.lerp(mat.clearcoat, 0.78 + output * 0.18, 0.08);
    }

    if (haloRef.current) {
      haloRef.current.scale.setScalar(1.055 + output * 0.1 + (reveal ? 0.16 : 0));
      const haloMaterial = haloRef.current.material as THREE.MeshBasicMaterial;
      haloMaterial.opacity = 0.1 + output * 0.2 + (reveal ? 0.16 : 0);
    }

    if (dustRef.current) {
      const pos = dustRef.current.geometry.attributes.position as THREE.BufferAttribute;
      const seed = dustRef.current.geometry.attributes.seedOffset as THREE.BufferAttribute;
      const seedArray = seed.array as Float32Array;
      for (let i = 0; i < pos.count; i++) {
        const ix = i * 3;
        const sx = seedArray[ix];
        const sy = seedArray[ix + 1];
        const sz = seedArray[ix + 2];
        const x = dustBasePositions[ix];
        const y = dustBasePositions[ix + 1];
        const z = dustBasePositions[ix + 2];
        const drift = reducedMotion ? 0 : 0.0015 + active * 0.01;
        pos.setXYZ(
          i,
          x + Math.sin(time * 0.7 + i * 0.017) * drift + sx * active * 0.002,
          y + Math.cos(time * 0.6 + i * 0.019) * drift + sy * active * 0.002,
          z + Math.sin(time * 0.5 + i * 0.013) * drift + sz * active * 0.002,
        );
      }
      pos.needsUpdate = true;
      const dustMaterial = dustRef.current.material as THREE.PointsMaterial;
      dustMaterial.opacity = 0.16 + output * 0.42 + (generating ? 0.2 : 0);
      dustMaterial.size = 0.012 + output * 0.01 + (generating ? 0.006 : 0);
    }
  });

  return (
    <group ref={groupRef}>
      <points ref={dustRef} geometry={dustGeometry}>
        <pointsMaterial
          color="#d98a65"
          size={0.014}
          transparent
          opacity={0.18}
          depthWrite={false}
          blending={THREE.AdditiveBlending}
        />
      </points>
      <mesh ref={haloRef} geometry={geometry} scale={1.055}>
        <meshBasicMaterial
          color="#d9633e"
          transparent
          opacity={0.12}
          side={THREE.BackSide}
          depthWrite={false}
          blending={THREE.AdditiveBlending}
        />
      </mesh>
      <mesh ref={meshRef} geometry={geometry}>
        <meshPhysicalMaterial
          ref={materialRef}
          color="#8f220e"
          roughness={0.22}
          metalness={0.0}
          clearcoat={0.82}
          clearcoatRoughness={0.14}
          sheen={0.65}
          sheenRoughness={0.32}
          sheenColor="#e49a77"
          transmission={0.1}
          thickness={0.7}
          ior={1.35}
          specularIntensity={0.88}
          specularColor="#d97047"
          emissive="#b45433"
          emissiveIntensity={0.08}
        />
      </mesh>
    </group>
  );
}

function damp(value: number, floor: number): number {
  if (!Number.isFinite(value)) return 0;
  return THREE.MathUtils.clamp(Math.max(0, value - floor) * 1.7, 0, 1.2);
}
