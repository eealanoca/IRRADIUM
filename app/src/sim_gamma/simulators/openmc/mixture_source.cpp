// Importance sampling of a cylinder source with full angular support.
#include <cmath>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

#include "openmc/random_lcg.h"
#include "openmc/source.h"

static bool hits_box(const openmc::Position& origin, const openmc::Direction& direction,
                     const double* low, const double* high)
{
  const double points[] = {origin.x, origin.y, origin.z};
  const double directions[] = {direction.x, direction.y, direction.z};
  double entry = 0, exit = 1.e300;
  for (int axis = 0; axis < 3; ++axis) {
    if (std::abs(directions[axis]) < 1.e-15) {
      if (points[axis] < low[axis] || points[axis] > high[axis]) return false;
    } else {
      double a = (low[axis]-points[axis])/directions[axis];
      double b = (high[axis]-points[axis])/directions[axis];
      if (a > b) std::swap(a, b);
      entry = std::max(entry, a);
      exit = std::min(exit, b);
    }
  }
  return exit > entry;
}

static double box_solid_angle(const openmc::Position& origin, const double* low, const double* high)
{
  const double points[] = {origin.x, origin.y, origin.z};
  double angle = 0;
  bool outside = false;
  for (int axis = 0; axis < 3; ++axis) {
    double distance = 0;
    if (points[axis] < low[axis]) distance = low[axis]-points[axis];
    if (points[axis] > high[axis]) distance = points[axis]-high[axis];
    if (distance <= 0) continue;
    outside = true;
    const int first = (axis+1)%3, second = (axis+2)%3;
    for (int i = 0; i < 2; ++i) {
      for (int j = 0; j < 2; ++j) {
        const double u = (i ? high[first] : low[first])-points[first];
        const double v = (j ? high[second] : low[second])-points[second];
        const double term = std::atan2(u*v, distance*std::sqrt(distance*distance+u*u+v*v));
        angle += (i == j ? 1 : -1)*term;
      }
    }
  }
  return outside ? angle : 12.566370614359172954;
}

class MixtureSource final : public openmc::Source {
public:
  explicit MixtureSource(const std::string& parameters)
  {
    std::istringstream input(parameters);
    int count;
    input >> strength_ >> x_ >> y_ >> z_ >> radius_ >> height_;
    input >> axis_.x >> axis_.y >> axis_.z >> minimum_mu_ >> fraction_ >> count;
    if (!input || count < 1 || count > 100 || fraction_ <= 0 || fraction_ >= 1 ||
        minimum_mu_ <= -1 || minimum_mu_ >= 1 || radius_ <= 0 || height_ <= 0)
      throw std::invalid_argument("Invalid cylindrical mixture source");
    double cumulative = 0;
    for (int index = 0; index < count; ++index) {
      double energy, probability;
      input >> energy >> probability;
      if (!input || energy <= 0 || probability <= 0)
        throw std::invalid_argument("Invalid gamma emission spectrum");
      energies_.push_back(energy);
      cumulative += probability;
      probabilities_.push_back(cumulative);
    }
    for (double& probability : probabilities_) probability /= cumulative;
    if (input >> box_mode_) {
      for (int axis = 0; axis < 3; ++axis) input >> box_low_[axis];
      for (int axis = 0; axis < 3; ++axis) input >> box_high_[axis];
      if (!input) throw std::invalid_argument("Invalid source target box");
      for (int axis = 0; axis < 3; ++axis)
        if (!(box_high_[axis] > box_low_[axis])) throw std::invalid_argument("Invalid target extent");
    }
    const double norm = std::sqrt(axis_.x*axis_.x + axis_.y*axis_.y + axis_.z*axis_.z);
    if (!(norm > 0)) throw std::invalid_argument("Invalid source direction");
    axis_ = {axis_.x/norm, axis_.y/norm, axis_.z/norm};
    const openmc::Direction helper = std::abs(axis_.z) < .9 ?
      openmc::Direction{0, 0, 1} : openmc::Direction{0, 1, 0};
    tangent_ = {helper.y*axis_.z-helper.z*axis_.y,
                helper.z*axis_.x-helper.x*axis_.z,
                helper.x*axis_.y-helper.y*axis_.x};
    const double length = std::sqrt(tangent_.x*tangent_.x + tangent_.y*tangent_.y + tangent_.z*tangent_.z);
    tangent_ = {tangent_.x/length, tangent_.y/length, tangent_.z/length};
    bitangent_ = {axis_.y*tangent_.z-axis_.z*tangent_.y,
                  axis_.z*tangent_.x-axis_.x*tangent_.z,
                  axis_.x*tangent_.y-axis_.y*tangent_.x};
  }

  openmc::SourceSite sample(uint64_t* seed) const override
  {
    constexpr double two_pi = 6.2831853071795864769;
    openmc::SourceSite site;
    site.particle = openmc::ParticleType::photon;
    const double radial = radius_ * std::sqrt(openmc::prn(seed));
    const double phi = two_pi * openmc::prn(seed);
    site.r = {x_ + radial*std::cos(phi), y_ + radial*std::sin(phi),
              z_ + height_*openmc::prn(seed)};
    const double box_fraction = box_mode_ ? box_solid_angle(site.r, box_low_, box_high_)/(2*two_pi) : 0;
    const bool conditional_box = box_mode_ && box_fraction > 1.e-12 && box_fraction < 1;
    const bool focused = openmc::prn(seed) < fraction_;
    double mu;
    do {
      mu = focused ? minimum_mu_ + (1-minimum_mu_)*openmc::prn(seed) : -1 + 2*openmc::prn(seed);
      const double azimuth = two_pi * openmc::prn(seed);
      const double transverse = std::sqrt(std::max(0., 1-mu*mu));
      const double a = transverse*std::cos(azimuth), b = transverse*std::sin(azimuth);
      site.u = {mu*axis_.x + a*tangent_.x + b*bitangent_.x,
                mu*axis_.y + a*tangent_.y + b*bitangent_.y,
                mu*axis_.z + a*tangent_.z + b*bitangent_.z};
    } while (conditional_box && focused && !hits_box(site.r, site.u, box_low_, box_high_));
    const double solid_angle_fraction = conditional_box ? box_fraction : (1-minimum_mu_)/2;
    const bool in_target = conditional_box ? hits_box(site.r, site.u, box_low_, box_high_) : mu >= minimum_mu_;
    const double density_ratio = 1-fraction_ + (in_target ? fraction_/solid_angle_fraction : 0);
    site.wgt = 1/density_ratio;
    const double energy_sample = openmc::prn(seed);
    size_t index = 0;
    while (index+1 < probabilities_.size() && energy_sample >= probabilities_[index]) ++index;
    site.E = energies_[index];
    return site;
  }

private:
  double x_, y_, z_, radius_, height_, minimum_mu_, fraction_;
  int box_mode_ {0};
  double box_low_[3] {}, box_high_[3] {};
  openmc::Direction axis_, tangent_, bitangent_;
  std::vector<double> energies_, probabilities_;
};

extern "C" std::unique_ptr<openmc::Source> openmc_create_source(std::string parameters)
{
  return std::make_unique<MixtureSource>(parameters);
}
