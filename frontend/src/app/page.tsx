import Navigation from '@/components/Navigation'
import Link from 'next/link'

const currentYear = new Date().getFullYear()

const steps = [
  {
    number: '01',
    title: 'Upload the plan',
    body: 'A PNG, JPEG, or PDF floor plan is the only input. Supplying the overall width and length turns the reconstruction from estimated scale to measured scale.',
  },
  {
    number: '02',
    title: 'Detect and reconstruct',
    body: 'Walls are vectorised, enclosed rooms segmented, doorways located, and the whole layout extruded into a solid 3D house with ceilings, openings, and glazing.',
  },
  {
    number: '03',
    title: 'Furnish and validate',
    body: 'Room types are inferred and confirmed by you, then real 3D furniture is placed with wall anchoring, doorway clearances, and walkable-route checks before export.',
  },
  {
    number: '04',
    title: 'Walk the property',
    body: 'Open the walkthrough in any modern browser. First-person movement, gravity, collision, doorway passes, room labels, and mobile controls all run on the real scene.',
  },
]

const checks = [
  'Wall topology and room boundaries remain closed',
  'Every doorway connects the rooms it was detected between',
  'Spawn point is walkable and every reachable room is tested',
  'Furniture footprints stay inside their rooms and clear of routes',
  'GLB integrity, normals, and scene bounds are inspected',
  'Failures are repaired or reported instead of hidden',
]

const styles = [
  {
    name: 'Modern',
    note: 'Charcoal fabrics, walnut, muted teal accents',
    swatches: ['#59616b', '#7d5a3c', '#2f6f6f', '#dedbd3'],
  },
  {
    name: 'Minimalist',
    note: 'Warm white, pale oak, low-contrast stone',
    swatches: ['#d6d1c7', '#b6a187', '#e6e2da', '#c7c3b9'],
  },
  {
    name: 'Scandinavian',
    note: 'Light timber, soft wool, quiet greens',
    swatches: ['#dfe3dc', '#c49a6c', '#4c7a55', '#f3efe6'],
  },
]

function PlanIllustration() {
  return (
    <svg
      viewBox="0 0 520 420"
      className="h-full w-full"
      role="img"
      aria-label="Illustrative floor plan drawing"
    >
      <g fill="none" stroke="#23392e" strokeLinecap="square">
        <rect x="24" y="24" width="472" height="372" strokeWidth="7" />
        <path d="M24 196h188M212 24v172M212 196h284M340 196v200M212 300h128" strokeWidth="5" />
        <path d="M96 400v-72M24 324h72" strokeWidth="2.5" />
      </g>
      <g stroke="#a47b4c" strokeWidth="3" strokeLinecap="round">
        <path d="M212 196h34" />
        <path d="M340 260h34" />
        <path d="M172 300h40" />
      </g>
      <g fill="#23392e" opacity="0.09">
        <rect x="60" y="64" width="112" height="44" rx="3" />
        <rect x="60" y="236" width="88" height="52" rx="3" />
        <rect x="256" y="64" width="148" height="34" rx="3" />
        <rect x="256" y="236" width="56" height="88" rx="3" />
      </g>
      <g fill="#6b685f" fontSize="11" fontFamily="ui-sans-serif, system-ui" letterSpacing="1.4">
        <text x="52" y="52">LIVING</text>
        <text x="240" y="52">KITCHEN / DINING</text>
        <text x="52" y="228">BEDROOM</text>
        <text x="368" y="228">BATH</text>
        <text x="240" y="392">HALL</text>
      </g>
      <circle cx="410" cy="330" r="5" fill="#a47b4c" />
      <circle cx="410" cy="330" r="14" fill="none" stroke="#a47b4c" strokeWidth="1" strokeDasharray="3 4" />
    </svg>
  )
}

export default function Home() {
  return (
    <div className="min-h-screen bg-ivory">
      <Navigation />

      <main>
        <section className="relative overflow-hidden">
          <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(70%_60%_at_85%_0%,rgba(164,123,76,0.10),transparent_70%)]" />
          <div className="shell relative grid gap-12 pb-16 pt-14 lg:grid-cols-[1.05fr_0.95fr] lg:items-center lg:gap-16 lg:pb-24 lg:pt-20">
            <div className="animate-rise">
              <p className="eyebrow flex items-center gap-3">
                <span className="inline-block h-px w-8 bg-bronze" aria-hidden="true" />
                Floor plans into walkable homes
              </p>
              <h1 className="display mt-5 text-[42px] leading-[1.04] text-ink sm:text-[56px] lg:text-[64px]">
                Every room,
                <br />
                before the first visit.
              </h1>
              <p className="mt-6 max-w-xl text-[15px] leading-7 text-stone">
                RealEstAI turns a 2D floor plan into a furnished, validated, first-person 3D
                walkthrough you can open in a browser. Walls, room types, furniture, lighting, and
                navigation all come from the plan you upload — never from a template house.
              </p>
              <div className="mt-8 flex flex-wrap items-center gap-3">
                <Link href="/create-tour" className="btn btn-primary btn-lg">
                  Create a walkthrough
                </Link>
                <Link href="/tours" className="btn btn-secondary btn-lg">
                  Browse walkthroughs
                </Link>
              </div>
              <dl className="mt-10 grid max-w-lg grid-cols-2 gap-x-6 gap-y-4 border-t border-line pt-6 sm:grid-cols-3">
                <div>
                  <dt className="text-[10px] font-semibold uppercase tracking-[0.16em] text-stone-light">
                    Input
                  </dt>
                  <dd className="mt-1 text-[13px] text-ink">Plan only, no photos</dd>
                </div>
                <div>
                  <dt className="text-[10px] font-semibold uppercase tracking-[0.16em] text-stone-light">
                    Validation
                  </dt>
                  <dd className="mt-1 text-[13px] text-ink">Geometry + navigation</dd>
                </div>
                <div>
                  <dt className="text-[10px] font-semibold uppercase tracking-[0.16em] text-stone-light">
                    Rendering
                  </dt>
                  <dd className="mt-1 text-[13px] text-ink">Runs in the browser</dd>
                </div>
              </dl>
            </div>

            <div className="animate-rise delay-1">
              <figure className="card relative overflow-hidden p-3 shadow-lift">
                <div className="blueprint-grid relative overflow-hidden rounded-[10px] border border-line bg-surface">
                  <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
                    <span className="text-[10px] font-semibold uppercase tracking-[0.18em] text-stone">
                      Illustrative plan
                    </span>
                    <span className="flex items-center gap-1.5 text-[10px] text-stone-light">
                      <span className="h-1.5 w-1.5 rounded-full bg-bronze" />
                      Vectorised, then furnished
                    </span>
                  </div>
                  <div className="p-4 sm:p-6">
                    <PlanIllustration />
                  </div>
                  <figcaption className="flex flex-wrap items-center justify-between gap-2 border-t border-line px-4 py-2.5 text-[10px] text-stone-light">
                    <span>Walls · rooms · doorways · furniture bounds</span>
                    <span className="text-stone">Illustration, not a property</span>
                  </figcaption>
                </div>
              </figure>
              <div className="mt-4 grid grid-cols-3 divide-x divide-line rounded-[12px] border border-line bg-surface/70">
                {[
                  ['Walls', 'Vectorised'],
                  ['Spawn', 'Checked'],
                  ['Doors', 'Routed'],
                ].map(([label, value]) => (
                  <div key={label} className="px-3 py-3 text-center">
                    <p className="text-[9px] font-semibold uppercase tracking-[0.14em] text-stone-light">
                      {label}
                    </p>
                    <p className="mt-0.5 text-xs font-medium text-ink">{value}</p>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </section>

        <section className="border-y border-line bg-surface">
          <div className="shell py-16 lg:py-20">
            <div className="max-w-2xl">
              <p className="eyebrow">The pipeline</p>
              <h2 className="mt-3 text-[30px] leading-tight sm:text-[38px]">
                What actually happens after you upload
              </h2>
              <p className="mt-3 text-sm leading-6 text-stone">
                No photogrammetry, no stock scenes, and no photo capture. Each stage produces real
                artefacts you can inspect in the job diagnostics.
              </p>
            </div>
            <ol className="mt-10 grid gap-px overflow-hidden rounded-[14px] border border-line bg-line sm:grid-cols-2 lg:grid-cols-4">
              {steps.map((step) => (
                <li key={step.number} className="bg-surface p-6">
                  <p className="font-serif text-[15px] text-bronze">{step.number}</p>
                  <h3 className="mt-3 text-[19px] leading-snug">{step.title}</h3>
                  <p className="mt-2 text-[13px] leading-6 text-stone">{step.body}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section className="shell py-16 lg:py-24">
          <div className="grid gap-12 lg:grid-cols-[0.9fr_1.1fr] lg:gap-20">
            <div>
              <p className="eyebrow">Truthful by construction</p>
              <h2 className="mt-3 text-[30px] leading-tight sm:text-[38px]">
                A walkthrough that can prove itself
              </h2>
              <p className="mt-4 text-sm leading-7 text-stone">
                The appealing render is the last step, not the only one. Every job keeps its
                validation report, repair log, and floor-plan preview, so a buyer-facing tour is
                only published when the scene holds together.
              </p>
              <div className="mt-7 flex flex-wrap gap-2.5">
                <Link href="/create-tour" className="btn btn-primary">
                  Start a reconstruction
                </Link>
                <Link href="/tours" className="btn btn-ghost">
                  See existing jobs
                </Link>
              </div>
            </div>
            <ul className="grid gap-3 sm:grid-cols-2">
              {checks.map((check) => (
                <li
                  key={check}
                  className="flex items-start gap-3 rounded-[12px] border border-line bg-surface px-4 py-3.5"
                >
                  <span className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full border border-forest/20 bg-forest/[0.06] text-forest">
                    <svg viewBox="0 0 14 14" className="h-3 w-3" aria-hidden="true">
                      <path
                        d="m3 7.4 2.4 2.4L11 4.2"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="1.7"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  </span>
                  <span className="text-[13px] leading-5 text-ink-soft">{check}</span>
                </li>
              ))}
            </ul>
          </div>
        </section>

        <section className="border-y border-line bg-parchment/50">
          <div className="shell py-16 lg:py-20">
            <div className="flex flex-wrap items-end justify-between gap-4">
              <div className="max-w-xl">
                <p className="eyebrow">Interior direction</p>
                <h2 className="mt-3 text-[30px] leading-tight sm:text-[38px]">
                  Coordinated furnishing, not random props
                </h2>
                <p className="mt-3 text-sm leading-6 text-stone">
                  Each style applies a consistent palette of woods, fabrics, stone, metal, and
                  accents across every room. Changing style swaps materials and furniture without
                  touching walls, doors, or room boundaries.
                </p>
              </div>
              <Link href="/create-tour" className="btn btn-secondary">
                Choose a style
              </Link>
            </div>
            <div className="mt-10 grid gap-4 sm:grid-cols-3">
              {styles.map((style) => (
                <div key={style.name} className="card p-5">
                  <div className="flex gap-1.5">
                    {style.swatches.map((hex) => (
                      <span
                        key={hex}
                        className="h-9 flex-1 rounded-[6px] border border-black/5"
                        style={{ backgroundColor: hex }}
                      />
                    ))}
                  </div>
                  <h3 className="mt-4 text-lg">{style.name}</h3>
                  <p className="mt-1 text-xs leading-5 text-stone">{style.note}</p>
                  <p className="mt-3 text-[10px] uppercase tracking-[0.14em] text-stone-light">
                    Real engine palette
                  </p>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="shell py-16 lg:py-20">
          <div className="relative overflow-hidden rounded-[18px] bg-forest px-6 py-14 text-center shadow-lift sm:px-12">
            <div className="blueprint-grid pointer-events-none absolute inset-0 opacity-[0.14]" aria-hidden="true" />
            <div className="relative mx-auto max-w-2xl">
              <p className="text-[11px] font-semibold uppercase tracking-[0.22em] text-bronze-soft">
                Ready when you are
              </p>
              <h2 className="mt-4 text-[30px] leading-tight text-ivory sm:text-[40px]">
                Bring a floor plan. Leave with a walkthrough.
              </h2>
              <p className="mt-4 text-sm leading-7 text-ivory/70">
                Room detection, furnishing, self-repair, and navigation validation are handled by
                the engine. You review the room types once, then walk the result.
              </p>
              <div className="mt-8 flex flex-wrap justify-center gap-3">
                <Link
                  href="/create-tour"
                  className="btn btn-lg bg-ivory text-forest hover:bg-white"
                >
                  Create a walkthrough
                </Link>
                <Link
                  href="/dashboard"
                  className="btn btn-lg border border-ivory/25 text-ivory hover:bg-ivory/10"
                >
                  Open workspace
                </Link>
              </div>
            </div>
          </div>
        </section>
      </main>

      <footer className="border-t border-line bg-surface">
        <div className="shell flex flex-col gap-6 py-10 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="font-serif text-lg text-ink">
              RealEst<span className="text-bronze">AI</span>
            </p>
            <p className="mt-1 text-xs text-stone">
              Floor-plan reconstruction, furnishing, and walkthroughs.
            </p>
          </div>
          <nav className="flex flex-wrap gap-x-6 gap-y-2 text-xs text-stone" aria-label="Footer">
            <Link href="/dashboard" className="hover:text-ink">Dashboard</Link>
            <Link href="/tours" className="hover:text-ink">Walkthroughs</Link>
            <Link href="/create-tour" className="hover:text-ink">Create</Link>
          </nav>
          <p className="text-[11px] text-stone-light">© {currentYear} RealEstAI</p>
        </div>
      </footer>
    </div>
  )
}