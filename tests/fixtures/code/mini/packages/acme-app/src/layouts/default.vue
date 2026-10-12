<template>
  <div class="layout">
    <AppHeader />
    <RouterView />
    <app-footer />
  </div>
</template>

<script setup lang="ts">
import { RouterView } from "vue-router"
</script>
