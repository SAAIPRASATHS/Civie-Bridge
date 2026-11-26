/**
 * Photo attachment with client-side validation.
 *
 * The checks here mirror `app/core/uploads.py` exactly - the same accepted MIME
 * types, the same 8 MiB ceiling - so a citizen is told about a bad file before
 * an 8 MB upload round trip. They are a convenience, not a boundary: the
 * backend re-validates every byte (size, declared type, magic numbers and
 * decodability), and that is the check that actually decides.
 */

import { ImagePlus, X } from 'lucide-react'
import { useId, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { FieldMessage } from '@/components/ui/field'
import { formatBytes } from '@/lib/format'
import type { CitizenEvidenceInput } from '@/types'

/** Mirrors `settings.allowed_upload_types`. */
const ACCEPTED_TYPES = ['image/jpeg', 'image/png', 'image/webp']
/** Mirrors `settings.max_upload_bytes`. */
const MAX_BYTES = 8 * 1024 * 1024
/** A UI convenience: the backend sets no count limit. */
const MAX_PHOTOS = 5

export interface SelectedPhoto {
  id: string
  file: File
  previewUrl: string
}

/** Read one file as the base64 payload the submission endpoint expects. */
function readAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(new Error(`could not read ${file.name}`))
    reader.onload = () => {
      const result = reader.result
      if (typeof result !== 'string') {
        reject(new Error(`could not read ${file.name}`))
        return
      }
      // `readAsDataURL` yields "data:<type>;base64,<payload>".
      const comma = result.indexOf(',')
      resolve(comma === -1 ? result : result.slice(comma + 1))
    }
    reader.readAsDataURL(file)
  })
}

/** Build the request payload from the selected photos. */
export async function toEvidencePayload(photos: SelectedPhoto[]): Promise<CitizenEvidenceInput[]> {
  return Promise.all(
    photos.map(async (photo) => ({
      file_name: photo.file.name,
      content_base64: await readAsBase64(photo.file),
      content_type: photo.file.type || null,
    })),
  )
}

function reject(file: File, existing: SelectedPhoto[]): string | null {
  if (existing.length >= MAX_PHOTOS) {
    return `At most ${MAX_PHOTOS} photos can be attached to one complaint.`
  }
  if (file.size === 0) return `${file.name} is empty.`
  if (file.size > MAX_BYTES) {
    return `${file.name} is ${formatBytes(file.size)}, which exceeds the ${formatBytes(MAX_BYTES)} limit.`
  }
  if (!ACCEPTED_TYPES.includes(file.type)) {
    return `${file.name} is not a JPEG, PNG or WebP image.`
  }
  return null
}

export function EvidenceUploader({
  photos,
  onChange,
  disabled = false,
}: {
  photos: SelectedPhoto[]
  onChange: (photos: SelectedPhoto[]) => void
  disabled?: boolean
}) {
  const inputRef = useRef<HTMLInputElement | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const inputId = useId()

  const add = (fileList: FileList | null) => {
    if (!fileList || fileList.length === 0) return
    const accepted: SelectedPhoto[] = []
    let firstProblem: string | null = null
    let running = [...photos]

    for (const file of Array.from(fileList)) {
      const message = reject(file, running)
      if (message) {
        firstProblem ??= message
        continue
      }
      const photo: SelectedPhoto = {
        id: `${file.name}-${file.size}-${file.lastModified}-${running.length}`,
        file,
        previewUrl: URL.createObjectURL(file),
      }
      accepted.push(photo)
      running = [...running, photo]
    }

    setProblem(firstProblem)
    if (accepted.length > 0) onChange([...photos, ...accepted])
    if (inputRef.current) inputRef.current.value = ''
  }

  const remove = (id: string) => {
    const target = photos.find((photo) => photo.id === id)
    if (target) URL.revokeObjectURL(target.previewUrl)
    onChange(photos.filter((photo) => photo.id !== id))
    setProblem(null)
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <input
          ref={inputRef}
          id={inputId}
          type="file"
          accept={ACCEPTED_TYPES.join(',')}
          multiple
          disabled={disabled}
          className="sr-only"
          onChange={(event) => add(event.target.files)}
        />
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={disabled || photos.length >= MAX_PHOTOS}
          onClick={() => inputRef.current?.click()}
        >
          <ImagePlus className="size-3.5" /> Add photos
        </Button>
        <FieldMessage>
          JPEG, PNG or WebP, up to {formatBytes(MAX_BYTES)} each, maximum {MAX_PHOTOS} per
          complaint. The server re-checks every file it receives.
        </FieldMessage>
      </div>

      {problem ? (
        <p role="alert" className="text-xs text-[var(--destructive)]">
          {problem}
        </p>
      ) : null}

      {photos.length === 0 ? null : (
        <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
          {photos.map((photo) => (
            <li
              key={photo.id}
              className="group relative overflow-hidden rounded-lg border border-[var(--border)] bg-white"
            >
              <img
                src={photo.previewUrl}
                alt={photo.file.name}
                className="h-28 w-full object-cover"
              />
              <div className="p-2">
                <p className="truncate text-xs font-medium" title={photo.file.name}>
                  {photo.file.name}
                </p>
                <p className="text-[11px] text-[var(--muted-foreground)]">
                  {formatBytes(photo.file.size)}
                </p>
              </div>
              <button
                type="button"
                aria-label={`Remove ${photo.file.name}`}
                disabled={disabled}
                onClick={() => remove(photo.id)}
                className="absolute right-1.5 top-1.5 rounded-full bg-white/95 p-1 text-[var(--foreground)] shadow-sm transition-colors hover:bg-[var(--destructive-subtle)] hover:text-[var(--destructive)]"
              >
                <X className="size-3.5" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
