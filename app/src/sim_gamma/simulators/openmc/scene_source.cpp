// Full-support source importance sampling of the selected load and its neighbours.
#include <algorithm>
#include <array>
#include <cmath>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <vector>
#include "openmc/random_lcg.h"
#include "openmc/source.h"

namespace {
constexpr double PI = 3.14159265358979323846;
constexpr double ISO = 1 / (4 * PI);
struct Target {
  std::array<double, 3> low, high, area, center;
  double radius2;
};
double visible_area(const Target& box, const openmc::Position& r) {
  const double p[] = {r.x, r.y, r.z};
  double total = 0;
  for (int axis = 0; axis < 3; ++axis)
    if (p[axis] < box.low[axis] || p[axis] > box.high[axis]) total += box.area[axis];
  return total;
}
openmc::Direction isotropic(uint64_t* seed) {
  const double mu = 2 * openmc::prn(seed) - 1, phi = 2 * PI * openmc::prn(seed);
  const double transverse = std::sqrt(std::max(0., 1 - mu * mu));
  return {transverse * std::cos(phi), transverse * std::sin(phi), mu};
}
openmc::Direction sample_face(const Target& box, const openmc::Position& r, double area, uint64_t* seed) {
  if (area == 0) return isotropic(seed);
  const double p[] = {r.x, r.y, r.z};
  double choice = openmc::prn(seed) * area;
  int face = -1;
  for (int axis = 0; axis < 3; ++axis) {
    if (p[axis] >= box.low[axis] && p[axis] <= box.high[axis]) continue;
    face = axis;
    choice -= box.area[axis];
    if (choice <= 0) break;
  }
  double u[3];
  double norm2 = 0;
  for (int axis = 0; axis < 3; ++axis) {
    const double value = axis == face ? (p[axis] < box.low[axis] ? box.low[axis] : box.high[axis])
      : box.low[axis] + (box.high[axis] - box.low[axis]) * openmc::prn(seed);
    u[axis] = value - p[axis];
    norm2 += u[axis] * u[axis];
  }
  const double norm = std::sqrt(norm2);
  return {u[0] / norm, u[1] / norm, u[2] / norm};
}
double angular_pdf(const Target& box, const openmc::Position& r, const openmc::Direction& u, double area) {
  if (area == 0) return ISO; // An internal source uses an isotropic proposal.
  const double p[] = {r.x, r.y, r.z}, direction[] = {u.x, u.y, u.z};
  double enter = 0, leave = 1e300;
  int face = -1;
  for (int axis = 0; axis < 3; ++axis) {
    if (direction[axis] == 0) {
      if (p[axis] < box.low[axis] || p[axis] > box.high[axis]) return 0;
      continue;
    }
    double a = (box.low[axis] - p[axis]) / direction[axis];
    double b = (box.high[axis] - p[axis]) / direction[axis];
    if (a > b) std::swap(a, b);
    if (a > enter) { enter = a; face = axis; }
    leave = std::min(leave, b);
    if (leave <= enter) return 0;
  }
  if (face < 0) return 0;
  // Uniform visible area transformed to solid angle: dA/dOmega = r^2/|n.u|.
  return enter * enter / (area * std::abs(direction[face]));
}
class SceneSource final : public openmc::Source {
public:
  explicit SceneSource(const std::string& parameters) {
    std::istringstream input(parameters);
    int count, energy_count;
    input >> strength_ >> x_ >> y_ >> z_ >> radius_ >> height_ >> fraction_ >> target_share_ >> count >> energy_count;
    if (!input || count < 1 || count > 128 || energy_count < 1 || energy_count > 100 ||
        radius_ <= 0 || height_ <= 0 || fraction_ <= 0 || fraction_ >= 1 || target_share_ <= 0 || target_share_ >= 1)
      throw std::invalid_argument("Invalid scene source parameters");
    double cumulative = 0;
    for (int i = 0; i < energy_count; ++i) {
      double energy, probability;
      input >> energy >> probability;
      if (!input || energy <= 0 || probability <= 0) throw std::invalid_argument("Invalid gamma spectrum");
      energies_.push_back(energy); cumulative += probability; probabilities_.push_back(cumulative);
    }
    for (auto& probability : probabilities_) probability /= cumulative;
    for (int i = 0; i < count; ++i) {
      Target box;
      for (auto& v : box.low) input >> v;
      for (auto& v : box.high) input >> v;
      if (!input) throw std::invalid_argument("Incomplete target box");
      std::array<double, 3> size;
      box.radius2 = 0;
      for (int axis = 0; axis < 3; ++axis) {
        size[axis] = box.high[axis] - box.low[axis];
        if (!(size[axis] > 0)) throw std::invalid_argument("Invalid target extent");
        box.center[axis] = (box.high[axis] + box.low[axis]) / 2;
        box.radius2 += size[axis] * size[axis] / 4;
      }
      for (int axis = 0; axis < 3; ++axis) box.area[axis] = size[(axis + 1) % 3] * size[(axis + 2) % 3];
      boxes_.push_back(box);
    }
  }
  openmc::SourceSite sample(uint64_t* seed) const override {
    openmc::SourceSite site;
    site.particle = openmc::ParticleType::photon;
    const double radius = radius_ * std::sqrt(openmc::prn(seed)), phi = 2 * PI * openmc::prn(seed);
    site.r = {x_ + radius * std::cos(phi), y_ + radius * std::sin(phi), z_ + height_ * openmc::prn(seed)};
    std::array<double, 128> areas, weights;
    const double position[] = {site.r.x, site.r.y, site.r.z};
    double sum = 0;
    for (size_t i = 0; i < boxes_.size(); ++i) {
      areas[i] = visible_area(boxes_[i], site.r);
      double distance2 = boxes_[i].radius2;
      for (int axis = 0; axis < 3; ++axis) distance2 += std::pow(position[axis] - boxes_[i].center[axis], 2);
      weights[i] = areas[i] > 0 ? areas[i] / distance2 : 4 * PI;
      if (i > 0) sum += weights[i];
    }
    weights[0] = boxes_.size() == 1 ? 1 : target_share_;
    for (size_t i = 1; i < boxes_.size(); ++i) weights[i] *= (1 - target_share_) / sum;
    double norm = 0;
    for (size_t i = 0; i < boxes_.size(); ++i) norm += weights[i];
    if (openmc::prn(seed) < fraction_) {
      double draw = openmc::prn(seed) * norm;
      size_t selected = boxes_.size() - 1;
      for (size_t i = 0; i < boxes_.size(); ++i) { draw -= weights[i]; if (draw <= 0) { selected = i; break; } }
      site.u = sample_face(boxes_[selected], site.r, areas[selected], seed);
    } else site.u = isotropic(seed);
    double density = (1 - fraction_) * ISO;
    for (size_t i = 0; i < boxes_.size(); ++i)
      density += fraction_ * weights[i] / norm * angular_pdf(boxes_[i], site.r, site.u, areas[i]);
    site.wgt = ISO / density;
    const double draw = openmc::prn(seed);
    size_t energy = 0;
    while (energy + 1 < probabilities_.size() && draw >= probabilities_[energy]) ++energy;
    site.E = energies_[energy];
    return site;
  }
private:
  double x_, y_, z_, radius_, height_, fraction_, target_share_;
  std::vector<Target> boxes_;
  std::vector<double> energies_, probabilities_;
};
}
extern "C" std::unique_ptr<openmc::Source> openmc_create_source(std::string parameters) {
  return std::make_unique<SceneSource>(parameters);
}
