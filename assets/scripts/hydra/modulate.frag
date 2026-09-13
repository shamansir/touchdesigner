// hydra `modulate` -- type: combineCoord
// inputs: amount (0.1)
//
// TWO TOP inputs, order matters:
//   input 0 = source     (the chain being modulated)
//   input 1 = modulator  (hydra's _c0 -- the argument inside modulate(...))
// i.e.  a.modulate(b, amount)  ->  a into input 0, b into input 1.
//
// GLSL TOP setup:
//   Common page:  Output Resolution = Input
//                 Pixel Format = 16-bit float (RGBA)
//   Vectors page: amount -> parent().par.Amount

uniform float amount;

out vec4 fragColor;

// --- verbatim from hydra glsl-functions.js ---------------------------------
vec2 modulate(vec2 _st, vec4 _c0, float amount) {
   //  return fract(st+(_c0.xy-0.5)*amount);
   return _st + _c0.xy*amount;
}
// ---------------------------------------------------------------------------

void main() {
   vec2 st = vUV.st;
   vec4 c0 = texture(sTD2DInputs[1], st);      // modulator, sampled at current st
   vec2 st2 = modulate(st, c0, amount);
   fragColor = TDOutputSwizzle(texture(sTD2DInputs[0], fract(st2)));
}
