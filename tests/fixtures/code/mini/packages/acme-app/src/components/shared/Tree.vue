<template>
  <ul>
    <SharedTree v-for="child in children" :key="child" />
  </ul>
</template>

<script setup lang="ts">
defineProps<{ children: string[] }>()
</script>
