// hydra `osc` -- type: src
// inputs: frequency (60), sync (0.1), offset (0)
//
// GLSL TOP setup:
//   Common page:  Output Resolution = Custom  (match your hydra canvas for A/B)
//                 Pixel Format = 16-bit float (RGBA)
//   Vectors page: time      -> expression  absTime.seconds
//                 frequency -> parent().par.Frequency
//                 sync      -> parent().par.Sync
//                 offset    -> parent().par.Offset

uniform float time;
uniform float frequency;
uniform float sync;
uniform float offset;

out vec4 fragColor;

// --- verbatim from hydra glsl-functions.js ---------------------------------
vec4 osc(vec2 _st, float frequency, float sync, float offset) {
   vec2 st = _st;
   float r = sin((st.x-offset/frequency+time*sync)*frequency)*0.5  + 0.5;
   float g = sin((st.x+time*sync)*frequency)*0.5 + 0.5;
   float b = sin((st.x+offset/frequency+time*sync)*frequency)*0.5  + 0.5;
   return vec4(r, g, b, 1.0);
}
// ---------------------------------------------------------------------------

void main() {
   fragColor = TDOutputSwizzle(osc(vUV.st, frequency, sync, offset));
}
