"use client";

export default function Switch({
  checked, onChange, disabled,
}: { checked: boolean; onChange: (checked: boolean) => void; disabled?: boolean }) {
  return (
    <div className="relative shrink-0">
      <input
        type="checkbox" className="sr-only peer"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      <div className="w-10 h-6 bg-border rounded-full peer peer-checked:bg-accent transition-colors" />
      <div className="absolute left-1 top-1 w-4 h-4 bg-white rounded-full transition-transform peer-checked:translate-x-4" />
    </div>
  );
}
