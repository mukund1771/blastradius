import "./polyfill"
import pad, { fmt as format } from "~/helpers/utils/format"
import * as backend from "~/helpers/backend"
import type { Shape } from "~/types"
import { RestRequest, parse as parseRest } from "@acme/data"
import { useI18n } from "@composables/i18n"
import { legacy } from "../helpers/backend/legacy.js"
import { ref } from "vue"
import { debounce } from "lodash-es"

export class EnvService {
  load(name: string) {
    return import("~/helpers/backend/runner").then((m) => m.run)
  }

  loadDynamic(name: string) {
    return import(`./plugins/${name}`)
  }

  label(key: string) {
    return this.t("request.send") + useI18n()("x") + pad(1) + format(2)
  }

  t(key: string) {
    return key
  }

  use(shape: Shape, r: RestRequest) {
    return [backend, parseRest, legacy, ref, debounce, shape, r]
  }
}
