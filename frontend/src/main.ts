import { createApp } from 'vue'
import App from './App.vue'
import './style.css'
// DPlayer 1.27 は CSS を JS に同梱しているため、別途 CSS を import しない。
createApp(App).mount('#app')
