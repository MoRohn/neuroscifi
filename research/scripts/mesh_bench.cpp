#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <numeric>
#include <random>
#include <vector>

struct Edge { float weight, trace, drive, baseline; };
static_assert(sizeof(Edge) == 16);
using Clock = std::chrono::steady_clock;

inline void advance(Edge& edge) {
    edge.trace = .95f * edge.trace + .05f * edge.drive;
    edge.weight += .003f * (edge.trace - edge.weight) + .0002f * edge.baseline;
}
int main(int argc, char** argv) {
    const int seed = argc > 1 ? std::atoi(argv[1]) : 915;
    const int ntrials = argc > 2 ? std::atoi(argv[2]) : 15;
    std::mt19937 rng(seed);
    std::cout << "seed,edges,block,requested_density,active,trial,kernel,us_per_step\n";
    double checksum = 0;
    for (int n : {262144, 8388608}) {
        std::vector<Edge> edges(n);
        for (int i=0;i<n;++i)
            edges[i] = {float(i%67)/67, .05f, float(i%43)/43, .01f};
        std::vector<unsigned char> mask(n);
        std::vector<uint64_t> dirty((n+63)/64);
        for (int block : {1,64}) {
            for (double density : {.002,.01,.05,.25,.5,1.0}) {
                std::vector<int> blocks(n / block);
                std::iota(blocks.begin(), blocks.end(), 0);
                std::shuffle(blocks.begin(), blocks.end(),rng);
                blocks.resize(std::max(1, int(std::llround(blocks.size()*density))));
                std::vector<int> ordered;
                ordered.reserve(blocks.size()*block);
                std::fill(mask.begin(),mask.end(),0);
                for(int b:blocks) for(int j=0;j<block;++j) {
                    int i=b*block+j; ordered.push_back(i); mask[i]=1;
                }
                std::sort(ordered.begin(), ordered.end());
                std::vector<int> shuffled=ordered;
                std::shuffle(shuffled.begin(),shuffled.end(),rng);
                const int steps = n > 1000000 ? 2 : 6;
                #ifdef VERIFY_KERNELS
                auto snapshot=edges;
                auto expected=edges;
                for(int step=0;step<steps;++step)
                    for(int id:ordered)advance(expected[id]);
                #endif
                std::array<int,4> methods={0,1,2,3};
                for(int trial=-1;trial<ntrials;++trial) {
                    std::shuffle(methods.begin(),methods.end(),rng);
                    for(int method:methods) {
                        #ifdef VERIFY_KERNELS
                        edges=snapshot;
                        #endif
                        auto t0=Clock::now();
                        for(int t=0;t<steps;++t) {
                            if(method==0) {
                                if(ordered.size()==edges.size()) {
                                    for(auto& e:edges) advance(e);
                                } else {
                                    for(int i=0;i<n;++i) if(mask[i]) advance(edges[i]);
                                }
                            } else if(method==3) {
                                std::fill(dirty.begin(),dirty.end(),uint64_t{0});
                                for(int id:shuffled)
                                    dirty[id>>6] |= uint64_t{1}<<(id&63);
                                for(size_t block_id=0;block_id<dirty.size();++block_id) {
                                    uint64_t remaining=dirty[block_id];
                                    while(remaining) {
                                        int bit=__builtin_ctzll(remaining);
                                        advance(edges[64*block_id+bit]);
                                        remaining &= remaining-1;
                                    }
                                }
                            } else {
                                const auto& ids=method==1?ordered:shuffled;
                                for(int i:ids) advance(edges[i]);
                            }
                        }
                        auto elapsed=std::chrono::duration<double,std::micro>(Clock::now()-t0).count();
                        #ifdef VERIFY_KERNELS
                        for(int id=0;id<n;++id) {
                            if(std::abs(edges[id].weight-expected[id].weight)>2e-6f ||
                               std::abs(edges[id].trace-expected[id].trace)>2e-6f) {
                                std::cerr<<"Kernel disagrees at record "<<id<<'\n';
                                return 3;
                            }
                        }
                        #endif
                        if(trial>=0)std::cout<<seed<<','<<n<<','<<block<<','<<density<<','<<ordered.size()<<','<<trial<<','
                           <<(method==0?"scan":method==1?"ordered":method==2?"shuffled":"bitmap")<<','<<std::setprecision(9)<<elapsed/steps<<'\n';
                    }
                }
                checksum+=edges[ordered[ordered.size()/2]].weight;
                std::vector<int> sorted_copy(ordered.size());
                for(int t=0;t<(ntrials==0?0:7);++t){
                    auto start=Clock::now();
                    std::copy(shuffled.begin(),shuffled.end(),sorted_copy.begin());
                    std::sort(sorted_copy.begin(),sorted_copy.end());
                    auto us=std::chrono::duration<double,std::micro>(Clock::now()-start).count();
                    if(sorted_copy!=ordered)return 2;
                    std::cout<<seed<<','<<n<<','<<block<<','<<density<<','<<ordered.size()<<','<<t
                             <<",prepare_order,"<<us<<'\n';
                }
            }
        }
    }
    std::cerr << "checksum=" << checksum << '\n';
}
