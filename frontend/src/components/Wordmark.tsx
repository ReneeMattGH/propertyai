import Link from 'next/link'

export function Wordmark({ href = '/', tagline = 'Spatial Studio' }: { href?: string; tagline?: string }) {
  return (
    <Link href={href} className="shrink-0 rounded-md" aria-label="Crestion Labs home">
      <span className="flex items-center gap-2.5">
        <span className="relative grid h-9 w-9 place-items-center rounded-[10px] bg-forest text-ivory shadow-[0_1px_0_rgba(255,255,255,0.12)_inset]">
          <svg viewBox="0 0 24 24" className="h-[19px] w-[19px]" aria-hidden="true">
            <path
              d="M4 9.2 12 3.5l8 5.7V20h-5.6v-5.4H9.6V20H4V9.2Z"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.3"
              strokeLinejoin="round"
            />
            <path d="M4 12.4h5.6M14.4 12.4H20" stroke="currentColor" strokeWidth="1.1" />
          </svg>
        </span>
        <span className="flex flex-col leading-none">
          <span className="font-serif text-[19px] font-medium tracking-tight text-ink">
            Crestion <span className="text-bronze">Labs</span>
          </span>
          <span className="mt-1 text-[9px] font-semibold uppercase tracking-[0.24em] text-stone-light">
            {tagline}
          </span>
        </span>
      </span>
    </Link>
  )
}
