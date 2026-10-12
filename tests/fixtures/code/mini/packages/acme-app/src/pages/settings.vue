<template>
  <section>
    <h2>{{ t("settings.title") }}</h2>
  </section>
</template>

<script setup lang="ts">
import { useI18n } from "~/composables/i18n"

definePage({ meta: { layout: "empty" } })
const t = useI18n()
</script>
