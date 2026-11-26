/**
 * The public landing page.
 *
 * It is deliberately static - no API calls, no session - so it renders for a
 * visitor who has never signed in. Its one earnest claim is stated three times
 * in different words: the AI recommends, a human officer decides.
 */

import {
  ArrowRight,
  FileText,
  Landmark,
  Languages,
  ShieldCheck,
  Sparkles,
  UserCheck,
} from 'lucide-react'
import { Link } from 'react-router-dom'
import type { ReactNode } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { BrandLockup } from '@/components/layout/Logo'
import { useDocumentTitle } from '@/lib/hooks'

interface Step {
  icon: ReactNode
  title: string
  body: string
}

const STEPS: Step[] = [
  {
    icon: <FileText className="size-5" />,
    title: 'Submit',
    body: 'A citizen describes a civic problem in their own words - in English, Tamil, or Tamil typed in Latin script - and may add a location and photographs.',
  },
  {
    icon: <Sparkles className="size-5" />,
    title: 'AI analyses',
    body: 'Specialised agents read the complaint, classify it, look for duplicates and draft a resolution. Everything they produce is a recommendation, never a decision.',
  },
  {
    icon: <UserCheck className="size-5" />,
    title: 'An officer decides',
    body: 'A human officer reviews the recommendation and makes the official decision. No civic outcome is ever applied by the AI on its own.',
  },
]

export default function LandingPage() {
  useDocumentTitle('Welcome')

  return (
    <div className="min-h-dvh bg-[var(--background)] text-[var(--foreground)]">
      <header className="sticky top-0 z-30 border-b border-[var(--border)] bg-white/95 backdrop-blur">
        <div className="mx-auto flex w-full max-w-[1100px] items-center justify-between gap-4 px-4 py-3.5 sm:px-6">
          <BrandLockup subtitle="Prototype city" />
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="sm" asChild className="hidden sm:inline-flex">
              <Link to="/login">Officer sign-in</Link>
            </Button>
            <Button size="sm" asChild>
              <Link to="/login">
                Sign in <ArrowRight className="size-4" />
              </Link>
            </Button>
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-[1100px] space-y-16 px-4 py-14 sm:px-6 lg:space-y-20 lg:py-20">
        <section className="grid items-center gap-10 lg:grid-cols-2">
          <div className="space-y-6">
            <Badge tone="primary">Smart Governance Platform</Badge>
            <h1 className="text-4xl font-semibold leading-tight tracking-tight sm:text-5xl">
              <span className="sr-only">CivicBridge AI - </span>
              Report a civic problem. Let AI triage it. A human decides.
            </h1>
            <p className="max-w-xl text-base leading-relaxed text-[var(--muted-foreground)]">
              CivicBridge AI is the intelligent citizen grievance management and governance
              platform for the prototype city. It reads what you write, understands it in your
              language, and prepares a tidy recommendation for the officer responsible - so your
              complaint reaches the right desk faster.
            </p>
            <div className="flex flex-wrap items-center gap-3">
              <Button size="lg" asChild>
                <Link to="/login">
                  Sign in <ArrowRight className="size-4" />
                </Link>
              </Button>
              <Button size="lg" variant="outline" asChild>
                <Link to="/register">Create an account</Link>
              </Button>
            </div>
            <p className="text-sm text-[var(--muted-foreground)]">
              Working for the council?{' '}
              <Link
                to="/login"
                className="font-medium text-[var(--primary)] underline-offset-4 hover:underline"
              >
                Officer sign-in
              </Link>
            </p>
          </div>

          <Card className="border-[var(--primary-subtle)] bg-[var(--primary-subtle)]">
            <CardContent className="space-y-5 p-6">
              <div className="flex items-center gap-2 text-[var(--primary)]">
                <ShieldCheck className="size-5" />
                <p className="text-sm font-semibold uppercase tracking-wide">Advisory by design</p>
              </div>
              <ul className="space-y-3 text-sm text-[var(--accent-foreground)]">
                <li className="flex gap-2.5">
                  <Landmark className="mt-0.5 size-4 shrink-0" />
                  <span>The AI is never given the authority to resolve, reject or close a complaint.</span>
                </li>
                <li className="flex gap-2.5">
                  <UserCheck className="mt-0.5 size-4 shrink-0" />
                  <span>An officer confirms the category, the department and the resolution.</span>
                </li>
                <li className="flex gap-2.5">
                  <ShieldCheck className="mt-0.5 size-4 shrink-0" />
                  <span>Every suggestion carries its provenance, so a reader knows what produced it.</span>
                </li>
              </ul>
            </CardContent>
          </Card>
        </section>

        <section className="space-y-8">
          <div className="space-y-2">
            <p className="text-xs font-semibold uppercase tracking-[0.14em] text-[var(--primary)]">
              How it works
            </p>
            <h2 className="text-2xl font-semibold tracking-tight">
              Three steps, with a human at the end of them
            </h2>
          </div>
          <div className="grid gap-5 md:grid-cols-3">
            {STEPS.map((step, index) => (
              <Card key={step.title}>
                <CardHeader>
                  <div className="flex items-center gap-3">
                    <span className="flex size-10 items-center justify-center rounded-lg bg-[var(--primary-subtle)] text-[var(--primary)]">
                      {step.icon}
                    </span>
                    <span className="text-xs font-semibold uppercase tracking-[0.14em] text-[var(--muted-foreground)]">
                      Step {index + 1}
                    </span>
                  </div>
                  <CardTitle className="mt-2">{step.title}</CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="text-sm leading-relaxed text-[var(--muted-foreground)]">
                    {step.body}
                  </p>
                </CardContent>
              </Card>
            ))}
          </div>
        </section>

        <section className="grid gap-5 md:grid-cols-2">
          <Card>
            <CardHeader>
              <div className="flex items-center gap-2 text-[var(--primary)]">
                <Languages className="size-5" />
                <CardTitle>Two languages, one form</CardTitle>
              </div>
            </CardHeader>
            <CardContent className="space-y-3 text-sm text-[var(--muted-foreground)]">
              <p>
                Complaints are accepted in English and in Tamil. Tamil typed in Latin script
                (transliteration) is accepted too.
              </p>
              <p>
                The AI detects the language and translates the intent for the officer, so a
                complaint is never turned away for being written in the wrong script.
              </p>
              <p className="font-medium text-[var(--foreground)]">
                &ldquo;என் தெருவில் தண்ணீர் வரவில்லை&rdquo; is as valid as &ldquo;no water on my street&rdquo;.
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <div className="flex items-center gap-2 text-[var(--primary)]">
                <Sparkles className="size-5" />
                <CardTitle>What the AI will and will not do</CardTitle>
              </div>
            </CardHeader>
            <CardContent className="space-y-3 text-sm text-[var(--muted-foreground)]">
              <p>
                The AI will read your complaint, guess its category and priority, look for
                duplicates and suggest a resolution.
              </p>
              <p>
                The AI will not decide anything. Its output is labelled as a suggestion
                everywhere it appears, and an officer acts on it - or overrules it.
              </p>
            </CardContent>
          </Card>
        </section>
      </main>

      <footer className="border-t border-[var(--border)] bg-white">
        <div className="mx-auto flex w-full max-w-[1100px] flex-col gap-3 px-4 py-8 text-sm text-[var(--muted-foreground)] sm:flex-row sm:items-center sm:justify-between sm:px-6">
          <p className="flex items-center gap-2">
            <Landmark className="size-4 shrink-0" />
            A prototype for civic complaint handling. The data shown is synthetic and represents no
            real citizen.
          </p>
          <div className="flex items-center gap-4">
            <Link to="/login" className="hover:text-[var(--foreground)]">
              Sign in
            </Link>
            <Link to="/register" className="hover:text-[var(--foreground)]">
              Register
            </Link>
          </div>
        </div>
      </footer>
    </div>
  )
}
