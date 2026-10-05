import Markdown from 'react-markdown'
import rehypeSanitize from 'rehype-sanitize'
import type { Citation } from '@/api'

const CITE_PREFIX = '#cite-'

/** Turns [n] markers into links the renderer can intercept. Markers with no matching citation
 * (e.g. one the curator removed) are rendered inert. */
function linkMarkers(summary: string, citations: Citation[]): string {
  const known = new Set(citations.map((c) => c.n))
  return summary.replace(/\[(\d+)\](?!\()/g, (m, n: string) =>
    known.has(Number(n)) ? `[\\[${n}\\]](${CITE_PREFIX}${n})` : m,
  )
}

interface SummaryProps {
  summary: string
  citations: Citation[]
  onCite: (citation: Citation) => void
}

export function Summary({ summary, citations, onCite }: SummaryProps) {
  if (!summary.trim()) {
    return <p className="text-muted-foreground italic">No summary yet. Run ./build_ontology.sh to generate one.</p>
  }
  return (
    <div className="max-w-none space-y-3 leading-relaxed [&_code]:rounded [&_code]:bg-muted [&_code]:px-1 [&_h1]:text-xl [&_h2]:text-lg [&_h3]:font-semibold [&_ol]:list-decimal [&_ol]:pl-6 [&_ul]:list-disc [&_ul]:pl-6">
      <Markdown
        rehypePlugins={[rehypeSanitize]}
        components={{
          a: ({ href, children }) => {
            if (href?.startsWith(CITE_PREFIX)) {
              const n = Number(href.slice(CITE_PREFIX.length))
              const citation = citations.find((c) => c.n === n)
              return (
                <button
                  type="button"
                  data-testid={`cite-marker-${n}`}
                  className="mx-0.5 align-super text-xs font-semibold text-sky-600 hover:underline"
                  onClick={() => citation && onCite(citation)}
                >
                  {children}
                </button>
              )
            }
            return (
              <a href={href} target="_blank" rel="noreferrer noopener" className="text-sky-600 underline">
                {children}
              </a>
            )
          },
        }}
      >
        {linkMarkers(summary, citations)}
      </Markdown>
    </div>
  )
}
