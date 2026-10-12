<template>
  <div>
    <!-- <FakeTag /> must not become a tag use -->
    <HttpRequest />
    <SharedItem :label="$t('home.cta')" />
    <HoppSmartInput :placeholder="t('request.url')" title="Enter the endpoint" />
    <icon-lucide-x />
    <button :disabled="count > 3" @click="(n) => n > 1">Send</button>
    <template v-if="open">
      <p>{{ count < 3 ? t("state.open") : t('state.closed') }}</p>
    </template>
    <template #header>
      <span>Welcome back</span>
    </template>
    <component :is="'AppHeader'" />
    <component :is="IconThing" />
    <component :is="dynamicThing" />
    <h1>{{ t("home.title") }} {{ t(`tab.${current}`) }}</h1>
    <p>{{ t(open ? 'state.open' : 'state.closed') }}</p>
    <UnknownWidget />
  </div>
</template>

<script lang="ts">
export default { name: "HomePage" }
</script>

<script setup lang="ts">
import { ref } from "vue"
import IconThing from "~icons/lucide/thing"
import { useI18n } from "@composables/i18n"

// t("commented.out") is a comment, not a use
const t = useI18n()
const count = ref(0)
const open = ref(true)
const current = ref("rest")
const dynamicThing = ref(null)
</script>
