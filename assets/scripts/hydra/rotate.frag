// hydra `rotate` -- type: coord
// inputs: angle (10, RADIANS), speed (0)
//
// One TOP input: the chain being rotated.
//
// GLSL TOP setup:
//   Common page:  Output Resolution = Input
//                 Pixel Format = 16-bit float (RGBA)
//   Vectors page: time  -> expression  absTime.seconds
//                 angle -> parent().par.Angle
//                 speed -> parent().par.Speed

uniform float time;
uniform float angle;
uniform float speed;

out vec4 fragColor;

// --- verbatim from hydra glsl-functions.js ---------------------------------
vec2 rotate(vec2 _st, float angle, float speed) {
   vec2 xy = _st - vec2(0.5);
   float ang = angle + speed *time;
   xy = mat2(cos(ang),-sin(ang), sin(ang),cos(ang))*xy;
   xy += 0.5;
   return xy;
}
// ---------------------------------------------------------------------------

void main() {
   // a coord node samples its input at the transformed coordinate.
   // fract() stands in for hydra's wrap, which happens inside src()/prev().
   vec2 st = rotate(vUV.st, angle, speed);
   fragColor = TDOutputSwizzle(texture(sTD2DInputs[0], fract(st)));
}
