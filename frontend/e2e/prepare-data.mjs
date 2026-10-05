// Gives each Playwright run a fresh copy of the fixture data dir.
import { cpSync, rmSync } from 'node:fs'
import path from 'node:path'

const here = import.meta.dirname
const target = path.join(here, '.data')
rmSync(target, { recursive: true, force: true })
cpSync(path.join(here, 'fixtures'), target, { recursive: true })
