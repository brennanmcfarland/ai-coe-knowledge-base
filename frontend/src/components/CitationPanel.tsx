import { ExternalLink } from 'lucide-react'
import type { Citation } from '@/api'
import { Button } from '@/components/ui/button'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'

interface CitationPanelProps {
  citation: Citation | null
  onClose: () => void
}

export function CitationPanel({ citation, onClose }: CitationPanelProps) {
  return (
    // Non-modal so the summary stays readable (and clickable) beside the source.
    <Sheet modal={false} open={citation !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent side="right" className="w-full sm:max-w-lg" data-testid="citation-panel">
        {citation && (
          <>
            <SheetHeader>
              <SheetTitle>Source [{citation.n}]</SheetTitle>
              <SheetDescription>{citation.doc_title || 'ClickUp document'}</SheetDescription>
            </SheetHeader>
            <div className="flex flex-col gap-4 px-4">
              <blockquote className="border-l-4 pl-4 text-sm whitespace-pre-wrap text-muted-foreground">
                {citation.excerpt}
              </blockquote>
              <Button asChild className="self-start">
                <a href={citation.url} target="_blank" rel="noreferrer noopener" data-testid="open-in-clickup">
                  Open in ClickUp <ExternalLink />
                </a>
              </Button>
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  )
}
