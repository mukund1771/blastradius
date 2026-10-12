import { describe, it } from "vitest"
import { EnvService } from "../env.service"

describe("EnvService", () => {
  it("loads", () => {
    new EnvService().label("a")
    t("never.counted.in.tests")
  })
})
