import { fmt } from "../utils/format"

export interface RunOptions {
  retries: number
}

export const run = (opts: RunOptions) => fmt(opts.retries)
