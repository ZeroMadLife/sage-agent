<template>
  <span class="sage-avatar" aria-hidden="true">
    <span class="sage-hair"></span>
    <span class="sage-face">
      <span class="sage-bangs"></span>
      <span class="sage-eye left"></span>
      <span class="sage-eye right"></span>
      <span class="sage-smile"></span>
    </span>
    <span class="sage-collar"></span>
  </span>
</template>

<style scoped>
.sage-avatar {
  position: relative;
  display: block;
  flex: none;
  width: 32px;
  height: 32px;
  overflow: hidden;
  border: 1px solid color-mix(in srgb, var(--sage-warning) 45%, var(--sage-border));
  border-radius: 50%;
  background: color-mix(in srgb, var(--sage-warning-bg) 72%, var(--sage-surface));
  box-shadow: inset 0 0 0 2px color-mix(in srgb, var(--sage-surface) 55%, transparent);
}

.sage-hair {
  position: absolute;
  top: 4px;
  left: 5px;
  width: 22px;
  height: 25px;
  border-radius: 48% 48% 40% 40%;
  background: #e7bd63;
}

.sage-face {
  position: absolute;
  top: 8px;
  left: 8px;
  width: 16px;
  height: 17px;
  border-radius: 46% 46% 50% 50%;
  background: #ffe4ce;
}

.sage-bangs {
  position: absolute;
  top: -2px;
  left: -1px;
  width: 17px;
  height: 7px;
  border-radius: 55% 55% 45% 45%;
  background: #efc86f;
  transform: rotate(-4deg);
}

.sage-eye {
  position: absolute;
  top: 8px;
  width: 2px;
  height: 2px;
  border-radius: 50%;
  background: #4e4a4e;
}

.sage-eye.left { left: 4px; }
.sage-eye.right { right: 4px; }

.sage-smile {
  position: absolute;
  bottom: 3px;
  left: 6px;
  width: 5px;
  height: 3px;
  border-bottom: 1px solid #b86f72;
  border-radius: 0 0 50% 50%;
}

.sage-collar {
  position: absolute;
  right: 7px;
  bottom: -3px;
  left: 7px;
  height: 10px;
  border-radius: 50% 50% 0 0;
  background: #5a768d;
}
</style>
