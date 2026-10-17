import { useState, type ReactNode } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { Code2, Download, X } from 'lucide-react';
import type { Meta } from '../types';

interface Props {
  title: string;
  caption?: string;          // one data-generated sentence from the API, never typed by hand
  meta?: Meta;               // concept chip + "How this was computed" drawer
  loading?: boolean;
  error?: string;
  onExportPng?: () => void;
  className?: string;
  children?: ReactNode;
}

export function ChartCard({ title, caption, meta, loading, error, onExportPng, className, children }: Props) {
  const [open, setOpen] = useState(false);
  return (
    <motion.section
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
      className={`glass p-4 relative overflow-hidden ${className ?? ''}`}
    >
      <header className="flex items-start justify-between gap-3 mb-2">
        <div>
          <h3 className="text-sm font-semibold tracking-wide">{title}</h3>
          {meta && (
            <span className="mt-1 inline-block text-[11px] px-2 py-0.5 rounded-full border"
                  style={{ borderColor: 'var(--price)', color: 'var(--price)' }}>
              {meta.concept}
            </span>
          )}
        </div>
        <div className="flex gap-1">
          {onExportPng && (
            <button className="icon-btn" aria-label="Download chart as PNG" onClick={onExportPng}><Download size={16} /></button>
          )}
          {meta && (
            <button className="icon-btn" aria-label="Show how this was computed" onClick={() => setOpen(true)}><Code2 size={16} /></button>
          )}
        </div>
      </header>

      {loading ? <div className="skeleton h-56 rounded-xl" />
        : error ? <p className="text-sm" style={{ color: 'var(--muted)' }}>{error}</p>
        : children}

      {caption && !loading && !error && (
        <p className="mt-2 text-xs" style={{ color: 'var(--muted)' }}>{caption}</p>
      )}

      <AnimatePresence>
        {open && meta && (
          <motion.aside
            initial={{ opacity: 0, y: 24 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 24 }}
            transition={{ duration: 0.25 }}
            className="absolute inset-0 p-4 overflow-auto"
            style={{ background: 'var(--surface-solid)' }}
            aria-label="How this was computed"
          >
            <div className="flex justify-between items-center mb-2">
              <h4 className="text-sm font-semibold">How this was computed</h4>
              <button className="icon-btn" aria-label="Close" onClick={() => setOpen(false)}><X size={16} /></button>
            </div>
            <p className="text-xs mb-2" style={{ color: 'var(--muted)' }}>
              DWM concept: <b style={{ color: 'var(--text)' }}>{meta.concept}</b> · {meta.rows.toLocaleString('en-IN')} rows · {meta.ms} ms
            </p>
            {meta.note && <p className="text-xs mb-2">{meta.note}</p>}
            {meta.sql && <pre className="mono text-xs whitespace-pre-wrap p-3 rounded-lg" style={{ background: 'rgba(0,0,0,.25)' }}>{meta.sql}</pre>}
          </motion.aside>
        )}
      </AnimatePresence>
    </motion.section>
  );
}
