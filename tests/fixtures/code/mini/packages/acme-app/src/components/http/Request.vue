<template>
  <div>
    <HttpHeaders />
    <Item />
    <RestBadge />
    <Chart />
    <button>{{ t("request.send") }}</button>
  </div>
</template>

<script setup lang="ts">
import Item from "../shared/Item.vue"
import { RestBadge } from "@acme/data"
import { useI18n } from "~/composables/i18n"
import { lazyThing } from "./lazy.js"

const t = useI18n()
const Chart = defineAsyncComponent(() => import("../shared/Chart.vue"))
lazyThing(Chart)
</script>
