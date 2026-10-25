<script setup lang="ts">
import RulesSection from '../components/settings/RulesSection.vue'
import TranscriptionSection from '../components/settings/TranscriptionSection.vue'
import SummarySection from '../components/settings/SummarySection.vue'
import RecordingSection from '../components/settings/RecordingSection.vue'
import FormatsSection from '../components/settings/FormatsSection.vue'
import DisplaySection from '../components/settings/DisplaySection.vue'
import SocialSection from '../components/settings/SocialSection.vue'
import YtDlpSection from '../components/settings/YtDlpSection.vue'
</script>

<template>
  <section>
    <header class="page-head">
      <div>
        <h1>設定</h1>
        <p>録画ルール、取得・更新、文字起こし、要約、表示</p>
      </div>
    </header>
    <RulesSection />
    <TranscriptionSection />
    <SummarySection />
    <SocialSection />
    <RecordingSection />
    <YtDlpSection />
    <FormatsSection />
    <DisplaySection />
  </section>
</template>
