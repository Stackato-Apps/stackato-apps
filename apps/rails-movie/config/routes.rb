Movies::Application.routes.draw do
  resources :movies
  # You can have the root of your site routed with "root"
  # just remember to delete public/index.html.
   root :to => "movies#index"

  # See how all your routes lay out with "rake routes"
end
