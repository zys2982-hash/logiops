<script setup lang="ts">
/**
 * 认证类页面的共享外壳（登录 / 注册）：深蓝底 + 只用基础形状（点阵 / 圆 / 环 / 方 / 条）做层次。
 *
 * 抽出来的原因：登录页与注册页只差一张表单，外壳（背景几何 + 左侧品牌区 + 右侧卡片）完全一致；
 * 各写一套 CSS 迟早会走样（注册页原来就是一套旧样式，和登录页明显不是一个档次）。
 *
 * 配色：沿用平台蓝（--logiops-sidebar-active = #2f6fed）与深蓝底，不引入新色系。
 */
defineProps<{
  /** 卡片标题，如「欢迎回来」 */
  title: string
  /** 卡片副标题 */
  subtitle?: string
}>()

/** 左侧要点：每条序号用最基础的形状（方 / 圆 / 环）表达，不引入图标库 */
const highlights = [
  { shape: 'square', title: '异常全流程留痕', desc: '检测 · 处置 · 审批 · 审计，一条链可追溯' },
  { shape: 'circle', title: '风险按规则分级', desc: '客户等级 × 问题类型，实时算出当前风险' },
  { shape: 'ring', title: '人车随单同步', desc: '运输状态一变，车辆与司机状态同步更新' },
]
</script>

<template>
  <div class="auth-shell">
    <!-- 背景几何层：只用基础形状堆层次，不依赖任何图片 -->
    <div class="geo" aria-hidden="true">
      <span class="geo-grid" />
      <span class="geo-circle geo-circle--xl" />
      <span class="geo-circle geo-circle--md" />
      <span class="geo-ring" />
      <span class="geo-square" />
      <span class="geo-square geo-square--sm" />
      <span class="geo-bar" />
    </div>

    <div class="auth-layout">
      <!-- 左：品牌 + 形状化要点 -->
      <section class="brand-panel">
        <div class="brand-mark">
          <span class="mark-square" />
          <span class="mark-circle" />
          <span class="mark-ring" />
        </div>

        <h1 class="brand-title">LogiOps</h1>
        <p class="brand-subtitle">物流运输在途异常协同平台</p>
        <p class="brand-desc">把在途异常从发现、判定、处置到审计，收进同一条可追溯的链路。</p>

        <ul class="highlight-list">
          <li v-for="item in highlights" :key="item.title">
            <span class="hl-shape" :class="`hl-shape--${item.shape}`" />
            <span class="hl-text">
              <strong>{{ item.title }}</strong>
              <em>{{ item.desc }}</em>
            </span>
          </li>
        </ul>
      </section>

      <!-- 右：卡片（表单由页面通过默认插槽传入） -->
      <section class="form-panel">
        <div class="form-card">
          <span class="card-accent" aria-hidden="true" />

          <header class="form-head">
            <h2>{{ title }}</h2>
            <p v-if="subtitle">{{ subtitle }}</p>
          </header>

          <slot />

          <div v-if="$slots.footer" class="form-foot">
            <slot name="footer" />
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
/* ---------------------------------------------------------------- 底色 */
.auth-shell {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  min-height: 100%;
  overflow: hidden;
  background:
    radial-gradient(1100px 560px at 14% 16%, rgba(47, 111, 237, 0.55) 0%, transparent 58%),
    radial-gradient(900px 520px at 88% 84%, rgba(63, 134, 255, 0.4) 0%, transparent 62%),
    linear-gradient(135deg, #0b1a33 0%, #122a52 46%, #1b3f7d 100%);
}

/* ------------------------------------------------------------ 背景几何 */
.geo {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.geo-grid {
  position: absolute;
  inset: 0;
  background-image: radial-gradient(rgba(255, 255, 255, 0.18) 1px, transparent 1px);
  background-size: 28px 28px;
  -webkit-mask-image: radial-gradient(circle at 32% 32%, rgba(0, 0, 0, 0.95), transparent 72%);
  mask-image: radial-gradient(circle at 32% 32%, rgba(0, 0, 0, 0.95), transparent 72%);
  opacity: 0.55;
}

.geo-circle {
  position: absolute;
  border-radius: 50%;
}

.geo-circle--xl {
  width: 540px;
  height: 540px;
  top: -190px;
  left: -170px;
  background: radial-gradient(circle at 36% 36%, rgba(96, 165, 255, 0.5), rgba(47, 111, 237, 0) 70%);
  animation: floatY 18s ease-in-out infinite;
}

.geo-circle--md {
  width: 330px;
  height: 330px;
  right: -110px;
  bottom: -130px;
  background: radial-gradient(circle at 40% 40%, rgba(130, 195, 255, 0.42), rgba(47, 111, 237, 0) 70%);
  animation: floatY 22s ease-in-out infinite reverse;
}

.geo-ring {
  position: absolute;
  top: 7%;
  right: 11%;
  width: 380px;
  height: 380px;
  border-radius: 50%;
  border: 1px solid rgba(255, 255, 255, 0.18);
  animation: spin 46s linear infinite;
}

/* 环上一个小圆点，让旋转看得出来 */
.geo-ring::after {
  content: '';
  position: absolute;
  top: 14px;
  left: 50%;
  width: 10px;
  height: 10px;
  margin-left: -5px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.75);
}

.geo-square {
  position: absolute;
  left: 44%;
  bottom: -70px;
  width: 190px;
  height: 190px;
  border-radius: 30px;
  border: 1px solid rgba(255, 255, 255, 0.16);
  transform: rotate(24deg);
  animation: floatY 26s ease-in-out infinite;
  --tilt: 24deg;
}

.geo-square--sm {
  left: 7%;
  bottom: 13%;
  width: 76px;
  height: 76px;
  /* 原来用白色渐变，落在蓝底上渲染成一块"灰斑"，像没擦干净的残留。
     改成偏蓝的填充 + 一道浅边，才是"有意为之的基础形状"。 */
  border: 1px solid rgba(160, 200, 255, 0.3);
  border-radius: 18px;
  background: linear-gradient(135deg, rgba(110, 168, 255, 0.24), rgba(47, 111, 237, 0.05));
  box-shadow: 0 12px 30px rgba(6, 20, 44, 0.22);
  transform: rotate(-14deg);
  --tilt: -14deg;
}

.geo-bar {
  position: absolute;
  top: 71%;
  right: 17%;
  width: 220px;
  height: 8px;
  border-radius: 999px;
  background: linear-gradient(90deg, rgba(255, 255, 255, 0.34), rgba(255, 255, 255, 0));
  transform: rotate(-18deg);
}

/* ---------------------------------------------------------------- 布局 */
.auth-layout {
  position: relative;
  z-index: 1;
  display: grid;
  grid-template-columns: 1.05fr 0.95fr;
  gap: 56px;
  align-items: center;
  width: 100%;
  max-width: 1080px;
  padding: 48px 40px;
}

/* -------------------------------------------------------------- 左侧品牌 */
.brand-panel {
  color: #fff;
}

.brand-mark {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  height: 40px;
  margin-bottom: 22px;
}

.mark-square {
  width: 34px;
  height: 34px;
  border-radius: 9px;
  background: linear-gradient(135deg, #6ea8ff 0%, #2f6fed 100%);
  box-shadow: 0 8px 20px rgba(47, 111, 237, 0.45);
}

.mark-circle {
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.85);
}

.mark-ring {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  border: 3px solid rgba(255, 255, 255, 0.45);
}

.brand-title {
  margin: 0;
  font-size: 40px;
  font-weight: 700;
  letter-spacing: 1px;
}

.brand-subtitle {
  margin: 6px 0 0;
  font-size: 16px;
  color: rgba(255, 255, 255, 0.78);
}

.brand-desc {
  max-width: 420px;
  margin: 18px 0 0;
  font-size: 14px;
  line-height: 1.7;
  color: rgba(255, 255, 255, 0.6);
}

.highlight-list {
  margin: 34px 0 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.highlight-list li {
  display: flex;
  align-items: center;
  gap: 12px;
}

/* 序号形状：方 / 圆 / 环 —— 全是最基础几何 */
.hl-shape {
  flex: none;
  width: 14px;
  height: 14px;
  background: linear-gradient(135deg, #8fc0ff, #2f6fed);
}

.hl-shape--square {
  border-radius: 3px;
}

.hl-shape--circle {
  border-radius: 50%;
}

.hl-shape--ring {
  border-radius: 50%;
  background: transparent;
  border: 3px solid #6ea8ff;
  width: 15px;
  height: 15px;
}

.hl-text {
  display: flex;
  flex-direction: column;
  line-height: 1.45;
}

.hl-text strong {
  font-size: 14px;
  font-weight: 600;
  color: rgba(255, 255, 255, 0.94);
}

.hl-text em {
  font-size: 12px;
  font-style: normal;
  color: rgba(255, 255, 255, 0.55);
}

/* -------------------------------------------------------------- 右侧卡片 */
.form-panel {
  display: flex;
  justify-content: flex-end;
}

.form-card {
  position: relative;
  width: 100%;
  max-width: 400px;
  padding: 36px 34px 28px;
  border-radius: 18px;
  background: #fff;
  box-shadow:
    0 24px 60px rgba(6, 20, 44, 0.35),
    0 2px 8px rgba(6, 20, 44, 0.12);
  overflow: hidden;
}

/* 卡片顶部一条蓝色圆角矩形：用一个基础形状收住视觉焦点 */
.card-accent {
  position: absolute;
  top: 0;
  left: 0;
  width: 96px;
  height: 5px;
  border-radius: 0 0 6px 0;
  background: linear-gradient(90deg, #2f6fed, #6ea8ff);
}

.form-head h2 {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
  color: #1f2d3d;
}

.form-head p {
  margin: 6px 0 22px;
  font-size: 13px;
  /* 原为 #909399：白底上只有 3.08:1，13px 正文不达标（实测见 artifacts/contrast_probe.py）。
     #6b7280 为 4.83:1，既通过 WCAG AA 又仍是次要文字的观感。 */
  color: #6b7280;
}

/* 主按钮样式统一由外壳提供：登录 / 注册两页必须长得一样（插槽内容属于父作用域，故用 :deep） */
.form-card :deep(.submit-btn) {
  width: 100%;
  height: 44px;
  margin-top: 4px;
  border: none;
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 4px;
  background: linear-gradient(135deg, #2f6fed 0%, #4c8bff 100%);
}

.form-card :deep(.submit-btn:hover) {
  background: linear-gradient(135deg, #2a63d4 0%, #3f7fff 100%);
}

.form-foot {
  margin-top: 18px;
  font-size: 13px;
  text-align: center;
  /* 同 .form-head p：原 #909399 在 13px 下只有 3.08:1，换成 4.83:1 的 #6b7280 */
  color: #6b7280;
}

/* 占位符：Element Plus 默认 #a8abb2 在白底上只有 2.26:1（实测），肉眼就是"灰得看不清"。
   这里统一提到 4.83:1，同时保持"比正文浅"的层级。 */
.form-card :deep(.el-input__inner::placeholder) {
  color: #6b7280;
  opacity: 1;
}

/* 插槽里的链接属于父组件作用域，这里用 :deep 才能命中 */
.form-foot :deep(a) {
  margin-left: 4px;
  color: #2f6fed;
  font-weight: 600;
  text-decoration: none;
}

.form-foot :deep(a:hover) {
  text-decoration: underline;
}

/* ---------------------------------------------------------------- 动画 */
@keyframes floatY {
  0%,
  100% {
    transform: translateY(0) rotate(var(--tilt, 0deg));
  }
  50% {
    transform: translateY(-18px) rotate(var(--tilt, 0deg));
  }
}

@keyframes spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

/* 尊重系统的"减少动态效果"设置 */
@media (prefers-reduced-motion: reduce) {
  .geo-circle,
  .geo-ring,
  .geo-square {
    animation: none;
  }
}

/* -------------------------------------------------------------- 响应式 */
@media (max-width: 900px) {
  .auth-layout {
    grid-template-columns: 1fr;
    gap: 28px;
    padding: 32px 22px;
  }

  .brand-panel {
    text-align: center;
  }

  .brand-mark {
    justify-content: center;
  }

  .brand-desc,
  .highlight-list {
    display: none;
  }

  .brand-title {
    font-size: 30px;
  }

  .form-panel {
    justify-content: center;
  }

  .geo-ring,
  .geo-square {
    display: none;
  }
}
</style>
