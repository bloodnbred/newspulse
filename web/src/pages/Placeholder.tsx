export default function Placeholder({ title, phase }: { title: string; phase: string }) {
  return (
    <div className="glass p-8 mt-4">
      <h1 className="text-2xl font-semibold">{title}</h1>
      <p className="mt-2" style={{ color: 'var(--muted)' }}>
        This page is built in phase {phase} (see prompts/ui-phase-prompts.md).
      </p>
    </div>
  );
}
